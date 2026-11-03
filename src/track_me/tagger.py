"""Tagging web app: give old, GPS-less photos a location and/or a corrected date by
dragging them into lanes.

One page (``tagger.html``) plus a few JSON endpoints, served by Starlette (already a
dependency of FastMCP). Page 1 clusters the untagged photos by date; page 2 is the
multi-lane board. Lanes are only a draft in the browser until Save, which writes every
edit in one transaction (``Database.tag_manual``) and records one ``change_log``
entry. Thumbnails come from the cache directory written by ``track-me ingest
--thumbnails``.

There is no login here: bind to loopback and put an access proxy (Cloudflare Access)
in front if it is exposed.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Generator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from track_me import config
from track_me.db import Database, ManualEdit

UI_PATH = Path(__file__).resolve().parent / "tagger.html"

KEY_RE = re.compile(r"[0-9a-f]{40}")  # dedupe_key = sha1 hex
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
LATLNG_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[,\s]\s*(-?\d+(?:\.\d+)?)\s*$")
MAX_TAGS_PER_SAVE = 50_000
SAMPLES_PER_CLUSTER = 4


class ResolveError(Exception):
    """A lane's place could not be resolved to coordinates (message is user-facing)."""


# --------------------------------------------------------------------------- #
# Logic (plain functions, tested directly)                                     #
# --------------------------------------------------------------------------- #
def build_clusters(day_counts: list[tuple[str, int]], gap_days: int) -> list[dict]:
    """Group per-day counts into runs; a gap over ``gap_days`` starts a new cluster."""
    clusters: list[dict] = []
    last: date | None = None
    for day, n in day_counts:
        d = date.fromisoformat(day)
        if clusters and last is not None and (d - last).days <= gap_days:
            clusters[-1]["end"] = day
            clusters[-1]["count"] += n
        else:
            clusters.append({"start": day, "end": day, "count": n})
        last = d
    return clusters


def resolve_place(query: str, client: Any = None) -> dict:
    """Turn what the user pasted into ``{lat, lng, label}``.

    ``lat, lng`` is taken as-is (offline). Anything else -- a Google Maps plus code
    (``MP5W+C5 Minato City, Tokyo, Japan``) or a place name -- goes to the Google
    Geocoding API, which understands both."""
    q = query.strip()
    if not q:
        raise ResolveError("Enter a plus code, 'lat, lng' or a place name")
    if m := LATLNG_RE.match(q):
        lat, lng = float(m.group(1)), float(m.group(2))
        _check_coords(lat, lng)
        return {"lat": lat, "lng": lng, "label": f"{lat:.5f}, {lng:.5f}"}
    if client is None:
        client = _google_client()
    try:
        results = client.geocode(q, language="en")
    except Exception as e:
        raise ResolveError(f"Google lookup failed: {e}") from e
    if not results:
        raise ResolveError(f"No match for '{q}'")
    first = results[0]
    loc = first["geometry"]["location"]
    return {"lat": loc["lat"], "lng": loc["lng"], "label": first.get("formatted_address", q)}


# EXIF "Make" as cameras write it -> how people say it. Unlisted makes are kept as written.
_MAKES = {
    "NIKON": "Nikon",
    "OLYMPUS": "Olympus",
    "SONY": "Sony",
    "FUJIFILM": "Fujifilm",
    "SAMSUNG": "Samsung",
    "LGE": "LG",
    "CASIO": "Casio",
    "KODAK": "Kodak",
    "PENTAX": "Pentax",
    "MINOLTA": "Minolta",
}
# Fallback when a photo has no EXIF: file-name prefix -> camera family. Only prefixes that
# name one family (IMG_ / DSC are shared by many makes, so they stay as-is).
_PREFIX_CAMERAS = {
    "DSCN": "Nikon Coolpix",
    "DSCF": "Fujifilm FinePix",
    "PXL_": "Google Pixel",
    "CRW_": "Canon",
    "IMAG": "HTC",
}


def camera_label(make: str | None, model: str | None, file_name: str = "") -> str:
    """A short, friendly camera name for the prefix filter: EXIF make + model
    (``Nikon E885``, ``Sony DSC-W5``), else a known file-name prefix, else the prefix."""
    make = " ".join((make or "").split())
    model = " ".join((model or "").split())
    if make or model:
        first = make.split(" ")[0] if make else ""
        brand = _MAKES.get(first.upper(), make)
        if brand and model.upper().startswith(first.upper() + " "):
            model = model[len(first) + 1 :]  # "Canon EOS 30D" -> "EOS 30D"
        return " ".join(x for x in (brand, model) if x)
    prefix = re.sub(r"\d.*", "", file_name).upper()
    return _PREFIX_CAMERAS.get(prefix[:4], prefix) or "Unknown camera"


def _check_date(value: str) -> str:
    """A lane's new capture day: a real ``YYYY-MM-DD``, not in the future."""
    try:
        d = date.fromisoformat(value)
    except TypeError, ValueError:
        raise ResolveError(f"Invalid date: {value!r}") from None
    if not DATE_RE.fullmatch(value) or d > date.today():
        raise ResolveError(f"Invalid date: {value!r}")
    return value


def _check_coords(lat: float, lng: float) -> None:
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ResolveError(f"Coordinates out of range: {lat}, {lng}")


def _google_client() -> Any:
    if not config.GOOGLE_MAPS_API_KEY:
        raise ResolveError("GOOGLE_MAPS_API_KEY is not set (put it in .env)")
    import googlemaps

    return googlemaps.Client(key=config.GOOGLE_MAPS_API_KEY)


# --------------------------------------------------------------------------- #
# App                                                                          #
# --------------------------------------------------------------------------- #
def _error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def create_app(
    db_path: Path,
    thumbs_dir: Path,
    *,
    max_photos: int = config.TAG_MAX_PHOTOS,
    gap_days: int = config.TAG_CLUSTER_GAP_DAYS,
    geocode_client: Any = None,
    generic_dates: Collection[str] = config.TAG_GENERIC_DATES,
) -> Starlette:
    """Build the app. ``geocode_client`` (anything with ``.geocode()``) is for tests.
    ``generic_dates`` are placeholder days that may be re-dated without confirmation."""
    client_box: list[Any] = [geocode_client]

    @contextmanager
    def open_db() -> Generator[Database]:
        # One short-lived connection per request: Starlette runs sync endpoints in a
        # thread pool and sqlite3 connections are tied to their creating thread.
        db = Database(db_path)
        try:
            yield db
        finally:
            db.close()

    def index(_: Request) -> Response:
        return HTMLResponse(UI_PATH.read_text(encoding="utf-8"))

    def clusters(_: Request) -> Response:
        with open_db() as db:
            day_counts = db.untagged_day_counts()
            found = build_clusters(day_counts, gap_days)
            for c in found:
                photos = db.untagged_photos(c["start"], c["end"], SAMPLES_PER_CLUSTER)
                c["samples"] = [p["dedupe_key"] for p in photos]
                c["generic"] = sum(
                    n
                    for day, n in day_counts
                    if c["start"] <= day <= c["end"] and day in generic_dates
                )
        return JSONResponse(
            {
                "max_photos": max_photos,
                "gap_days": gap_days,
                "total": sum(c["count"] for c in found),
                "clusters": found,
            }
        )

    def photos(request: Request) -> Response:
        start = request.query_params.get("start", "")
        end = request.query_params.get("end", "")
        if not (DATE_RE.fullmatch(start) and DATE_RE.fullmatch(end)):
            return _error("start and end must be YYYY-MM-DD")
        with open_db() as db:
            total = db.count_untagged(start, end)
            rows = db.untagged_photos(start, end, max_photos)
        return JSONResponse(
            {
                "total": total,
                "shown": len(rows),
                "max_photos": max_photos,
                "photos": [
                    {
                        "key": r["dedupe_key"],
                        "date": r["local_date"],
                        "taken_at": r["taken_at"],
                        "name": r["file_name"],
                        "url": r["google_photos_url"],
                        "camera": camera_label(r["make"], r["model"], r["file_name"]),
                        "generic": r["local_date"] in generic_dates,
                        "trusted": r["local_date"] not in generic_dates
                        and r["taken_at_source"] != "manual",
                    }
                    for r in rows
                ],
            }
        )

    async def resolve(request: Request) -> Response:
        try:
            body = await request.json()
            query = str(body["query"])
        except Exception:
            return _error('expected JSON like {"query": "..."}')
        try:
            if client_box[0] is None and not LATLNG_RE.match(query.strip()) and query.strip():
                client_box[0] = _google_client()
            return JSONResponse(resolve_place(query, client_box[0]))
        except ResolveError as e:
            return _error(str(e), 422)

    async def tags(request: Request) -> Response:
        try:
            body = await request.json()
            edits: list[ManualEdit] = []
            labels: list[str] = []
            for lane in body["lanes"]:
                keys = lane["keys"]
                if not all(isinstance(k, str) and KEY_RE.fullmatch(k) for k in keys):
                    return _error("invalid photo key")
                lat, lng = lane.get("lat"), lane.get("lng")
                if (lat is None) != (lng is None):
                    return _error("lat and lng go together")
                if lat is not None:
                    lat, lng = float(lat), float(lng)
                    _check_coords(lat, lng)
                day = _check_date(lane["date"]) if lane.get("date") else None
                if lat is None and day is None:
                    if keys:
                        return _error("a lane needs a place, a date, or both")
                    continue
                edits += [ManualEdit(k, lat, lng, day) for k in keys]
                if keys:
                    labels.append(str(lane.get("label", ""))[:120])
            confirm = body.get("confirm_overwrite") is True
        except ResolveError as e:
            return _error(str(e), 422)
        except Exception:
            return _error('expected {"lanes": [{"lat", "lng", "date", "label", "keys"}]}')
        if len(edits) > MAX_TAGS_PER_SAVE:
            return _error("too many photos in one save", 413)
        if not edits:
            return JSONResponse({"tagged": 0, "skipped": 0})

        with open_db() as db:
            trusted = db.trusted_dates([e.key for e in edits if e.date], generic_dates)
            if trusted and not confirm:
                return JSONResponse(
                    {
                        "error": f"{len(trusted)} photos already have a trusted date",
                        "needs_confirmation": True,
                        "trusted": len(trusted),
                        "dates": sorted(set(trusted.values())),
                    },
                    status_code=409,
                )
            done = set(db.tag_manual(edits))
            if done:
                located = sum(1 for e in edits if e.key in done and e.lat is not None)
                redated = sum(1 for e in edits if e.key in done and e.date)
                db.log_change(
                    "tag",
                    f"tag: {len(done)} photos manually edited "
                    f"({located} located, {redated} re-dated)",
                    {
                        "photos": len(done),
                        "located": located,
                        "redated": redated,
                        "overwrote_trusted_dates": sum(1 for k in trusted if k in done),
                        "skipped": len(edits) - len(done),
                        "places": labels[:50],
                    },
                )
        return JSONResponse({"tagged": len(done), "skipped": len(edits) - len(done)})

    def thumb(request: Request) -> Response:
        key = request.path_params["key"]
        path = thumbs_dir / f"{key}.jpg"
        if not KEY_RE.fullmatch(key) or not path.is_file():
            return Response(status_code=404)
        return FileResponse(
            path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"}
        )

    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/clusters", clusters),
            Route("/api/photos", photos),
            Route("/api/resolve", resolve, methods=["POST"]),
            Route("/api/tags", tags, methods=["POST"]),
            Route("/thumb/{key}", thumb),
        ]
    )


def run(*, db_path: Path, host: str, port: int) -> None:
    import uvicorn

    app = create_app(db_path, config.THUMBNAIL_CACHE_DIR)
    uvicorn.run(app, host=host, port=port, log_level="info")
