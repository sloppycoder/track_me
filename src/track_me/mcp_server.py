"""MCP server: read-only photo-catalog tools for chat clients (ChatGPT, Claude …).

The chat model asks the questions; these tools answer them from the SQLite
catalog (``media`` + ``place``), which is opened read-only per call. Tool logic
lives in plain functions that take a :class:`Database` (so tests drive them
directly); :func:`create_server` wraps them as FastMCP tools.

Over HTTP the server is protected by Google OAuth (FastMCP's OAuth proxy, which
gives MCP clients the dynamic-client-registration flow they expect) and only the
emails in ``MCP_ALLOWED_EMAILS`` get through — see :class:`AllowlistGoogleProvider`.

Run:
    track-me mcp                  # HTTP on 127.0.0.1:8080/mcp, Google OAuth
    track-me mcp --no-auth        # local testing only (loopback host required)
    track-me mcp --stdio          # local MCP clients over stdio; no auth needed

Over stdio the client launches the server as a subprocess on the user's own
machine, so there is no network listener to protect. The catalog is read from
``config.DB_PATH`` (default ``./userdata/track_me.db``, relative to the CWD).
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import date, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from fastmcp import FastMCP
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.providers.google import GoogleProvider
from mcp.types import ToolAnnotations
from pydantic import Field

from track_me import config, storage
from track_me import timeline as tl
from track_me.db import Database, from_iso

logger = logging.getLogger(__name__)

# Caps keep tool results small enough for a chat model's context.
MAX_PHOTOS = 200
MAX_STAYS = 300
MAX_GROUPS = 200
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

INSTRUCTIONS = """\
Tools over the user's personal photo catalog (from a Google Photos export): when
and where each photo was taken, with place names from reverse geocoding. Use them
to answer questions about the user's travels, trips and where they were when.

- Dates are the photo's LOCAL day, YYYY-MM-DD, inclusive on both ends.
- Countries are ISO-2 codes (JP, US, SG …).
- Call catalog_overview first to learn the date span and which countries exist.
- City names are stored as Google returned them (e.g. "Hà Nội"); call
  find_places to get the exact spelling before filtering by city.
- get_trips groups photos into contiguous stays (country or city level); a stay
  in the user's home country/city between trips is normal.
- Each stay has `home` (where the user lived at that time) and `is_travel`. The user
  has NOT always had a home: during some periods (e.g. a roaming stretch with no
  base) `home` is null, and then every stay is `is_travel: true`. Null `home` means
  "no home then", not missing data — do not read all of it as trips or holidays.
- Each photo has a google_photos_url (opens the original in Google Photos) and a
  maps_url (opens the spot in Google Maps). Offer them as links when useful.
"""


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def maps_url(lat: float, lng: float) -> str:
    """A Google Maps link for a coordinate (no API key; opens the app on mobile)."""
    return f"https://www.google.com/maps/search/?api=1&query={lat:.5f},{lng:.5f}"


def _local_time(taken_at: str, tz: str | None) -> str:
    """The capture time in the photo's own timezone, minute resolution."""
    dt = from_iso(taken_at)
    if dt is None:
        return taken_at
    if tz:
        try:
            dt = dt.astimezone(ZoneInfo(tz))
        except KeyError, ValueError:
            pass
    return dt.strftime("%Y-%m-%d %H:%M %Z").strip()


def _sample(rows: list, limit: int) -> list:
    """Up to ``limit`` rows spread evenly across ``rows`` (keeps first and last)."""
    if len(rows) <= limit:
        return rows
    if limit <= 1:
        return rows[:limit]
    step = (len(rows) - 1) / (limit - 1)
    return [rows[round(i * step)] for i in range(limit)]


def _city(row: dict) -> str | None:
    return tl.city_label(
        {"cc": row["country_code"], "city": row["city"], "admin1": row["admin1"]}
    )


def _photo(row: dict) -> dict:
    return {
        "id": row["id"],
        "file_name": row["file_name"],
        "kind": row["kind"],
        "local_time": _local_time(row["taken_at"], row["timezone"]),
        "local_date": row["local_date"],
        "lat": round(row["latitude"], 5),
        "lng": round(row["longitude"], 5),
        "city": _city(row),
        "country": row["country_code"],
        "address": row["formatted_address"],
        "google_photos_url": row["google_photos_url"],
        "maps_url": maps_url(row["latitude"], row["longitude"]),
    }


# --------------------------------------------------------------------------- #
# Tool logic                                                                   #
# --------------------------------------------------------------------------- #
def catalog_overview(db: Database) -> dict:
    """Totals, date span and per-country photo counts for the whole catalog."""
    return {"today": date.today().isoformat(), **db.catalog_summary()}


def find_places(db: Database, query: str, limit: int = 25) -> dict:
    """Stored place names (city/region/address) containing ``query``."""
    places = db.search_places(query, limit=min(limit, MAX_GROUPS))
    return {"query": query, "places": places}


def find_photos(
    db: Database,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    country: str | None = None,
    city: str | None = None,
    near_lat: float | None = None,
    near_lng: float | None = None,
    radius_km: float = 5.0,
    limit: int = 50,
) -> dict:
    """Photos matching the filters; returns the total match count plus up to
    ``limit`` photos sampled evenly across the matching time span."""
    rows = db.search_located(start_date=start_date, end_date=end_date, country=country, city=city)
    if near_lat is not None and near_lng is not None:
        rows = [
            r
            for r in rows
            if tl.haversine_km(near_lat, near_lng, r["latitude"], r["longitude"]) <= radius_km
        ]
    picked = _sample(rows, max(1, min(limit, MAX_PHOTOS)))
    return {
        "total_matches": len(rows),
        "returned": len(picked),
        "sampled": len(picked) < len(rows),
        "first_date": rows[0]["local_date"] if rows else None,
        "last_date": rows[-1]["local_date"] if rows else None,
        "photos": [_photo(r) for r in picked],
    }


def get_trips(
    db: Database,
    *,
    start_date: str,
    end_date: str,
    level: Literal["country", "city"] = "country",
    country: str | None = None,
    merge_km: float = 50.0,
    min_hours: int = 24,
    travel_only: bool = False,
) -> dict:
    """Chronological, non-overlapping stays (the timeline engine, see timeline.py).
    Each stay carries ``home`` (label of where the user lived then, or None) and
    ``is_travel`` (False = within 100 mi of that home). Home can be absent for some
    periods; with no home, ``is_travel`` is always True."""
    # build_stays compares UTC taken_at against a half-open [start, end) range.
    end_exclusive = (date.fromisoformat(end_date) + timedelta(days=1)).isoformat()
    stays = tl.build_stays(
        start_date,
        end_exclusive,
        level=level,
        region=[country] if country else None,
        merge_km=merge_km,
        min_hours=min_hours,
        db=db,
        travel_only=travel_only,
    )
    for s in stays:
        s["maps_url"] = maps_url(s["lat"], s["lng"])
    return {
        "level": level,
        "stay_count": len(stays),
        "truncated": len(stays) > MAX_STAYS,
        "stays": stays[:MAX_STAYS],
    }


def place_stats(
    db: Database,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    group_by: Literal["country", "city"] = "country",
    country: str | None = None,
    limit: int = 50,
) -> dict:
    """Photo count, distinct days and first/last date per country or city."""
    rows = db.search_located(start_date=start_date, end_date=end_date, country=country)
    groups: dict[tuple, dict] = {}
    for r in rows:
        if group_by == "city":
            key = (_city(r), r["country_code"])
        else:
            key = (r["country_code"],)
        if key[0] is None:
            continue
        g = groups.setdefault(key, {"days": set(), "count": 0, "first": None, "last": None})
        g["count"] += 1
        g["days"].add(r["local_date"])
        g["first"] = g["first"] or r["local_date"]
        g["last"] = r["local_date"]
    ranked = sorted(groups.items(), key=lambda kv: kv[1]["count"], reverse=True)
    out = []
    for key, g in ranked[: min(limit, MAX_GROUPS)]:
        item = {"city": key[0], "country": key[1]} if group_by == "city" else {"country": key[0]}
        out.append(
            {
                **item,
                "photo_count": g["count"],
                "days_with_photos": len(g["days"]),
                "first_date": g["first"],
                "last_date": g["last"],
            }
        )
    return {"group_by": group_by, "group_count": len(groups), "groups": out}


def busiest_days(db: Database, *, start_date: str, end_date: str, limit: int = 10) -> dict:
    """The local days with the most photos in a window, with where they were."""
    rows = db.search_located(start_date=start_date, end_date=end_date)
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(r["local_date"], []).append(r)
    ranked = sorted(by_day.items(), key=lambda kv: len(kv[1]), reverse=True)[:limit]
    days = []
    for day, rs in ranked:
        cities = Counter(c for c in (_city(r) for r in rs) if c)
        days.append(
            {
                "date": day,
                "photo_count": len(rs),
                "cities": [c for c, _ in cities.most_common(3)],
                "countries": sorted({r["country_code"] for r in rs if r["country_code"]}),
            }
        )
    return {"days": days}


# --------------------------------------------------------------------------- #
# Auth                                                                         #
# --------------------------------------------------------------------------- #
class AllowlistGoogleProvider(GoogleProvider):
    """Google OAuth that only admits a fixed set of (verified) email addresses.

    Anyone with a Google account can complete the sign-in, so the allowlist is
    what actually restricts access. It is checked on every request, after the
    token has been validated against Google — a token for any other account is
    treated as invalid (401), so not even ``tools/list`` gets through.
    """

    def __init__(self, *, allowed_emails: frozenset[str], **kwargs):
        if not allowed_emails:
            raise ValueError("allowed_emails must not be empty")
        kwargs.setdefault("required_scopes", ["openid", "email"])
        super().__init__(**kwargs)
        self.allowed_emails = frozenset(e.lower() for e in allowed_emails)

    def is_allowed(self, token: AccessToken | None) -> bool:
        if token is None:
            return False
        claims = token.claims or {}
        email = (claims.get("email") or "").lower()
        return bool(claims.get("email_verified")) and email in self.allowed_emails

    async def load_access_token(self, token: str) -> AccessToken | None:
        validated = await super().load_access_token(token)
        if validated is None:
            return None
        # FastMCP's AccessToken carries the Google claims; anything else fails closed.
        if not isinstance(validated, AccessToken) or not self.is_allowed(validated):
            logger.warning("Rejected MCP token for a non-allowlisted account")
            return None
        return validated


def build_auth() -> AllowlistGoogleProvider:
    """The Google OAuth provider from config; raises if anything is missing."""
    missing = [
        name
        for name, value in (
            ("MCP_BASE_URL", config.MCP_BASE_URL),
            ("GOOGLE_OAUTH_CLIENT_ID", config.GOOGLE_OAUTH_CLIENT_ID),
            ("GOOGLE_OAUTH_CLIENT_SECRET", config.GOOGLE_OAUTH_CLIENT_SECRET),
            ("MCP_ALLOWED_EMAILS", config.MCP_ALLOWED_EMAILS),
        )
        if not value
    ]
    if missing:
        raise SystemExit(
            f"MCP OAuth not configured; set {', '.join(missing)} (or use --no-auth locally)"
        )
    return AllowlistGoogleProvider(
        client_id=config.GOOGLE_OAUTH_CLIENT_ID,
        client_secret=config.GOOGLE_OAUTH_CLIENT_SECRET,
        base_url=config.MCP_BASE_URL,
        jwt_signing_key=config.MCP_JWT_SIGNING_KEY,
        allowed_emails=config.MCP_ALLOWED_EMAILS,
    )


# --------------------------------------------------------------------------- #
# Server                                                                       #
# --------------------------------------------------------------------------- #
DateStr = Annotated[str, Field(description="Local date, YYYY-MM-DD (inclusive)")]
OptDate = Annotated[str | None, Field(description="Local date, YYYY-MM-DD (inclusive)")]
OptCountry = Annotated[str | None, Field(description="ISO-2 country code, e.g. JP")]
_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def _open_db() -> Database:
    """A fresh read-only, immutable connection to the catalog.

    Immutable means SQLite takes no locks at all (and never creates -journal/-wal
    files), so the server can never block an ingest/geocode writing the same file,
    nor be blocked by one. A query that overlaps a commit could see a half-written
    state — the server never writes, so the file is never harmed, and the next
    call (a new connection) sees the committed data."""
    return Database.read_only(config.DB_PATH, immutable=True)


def fetch_db() -> None:
    """If ``TRACKME_DB_URI`` is set, copy the catalog from GCS to local disk and point
    ``config.DB_PATH`` at the copy (one sequential download per cold start)."""
    if not config.DB_GCS_URI:
        return
    logger.info("Copying catalog %s -> %s", config.DB_GCS_URI, config.DB_LOCAL_COPY)
    config.DB_PATH = storage.download_gcs(config.DB_GCS_URI, config.DB_LOCAL_COPY)
    logger.info("Catalog ready (%.1f MB)", config.DB_PATH.stat().st_size / 1e6)


def _require_db() -> None:
    if not config.DB_PATH.is_file():
        raise SystemExit(
            f"catalog not found at {config.DB_PATH} — run from the directory holding "
            "userdata/, or set TRACKME_USERDATA / DB_PATH"
        )


def create_server(auth: GoogleProvider | None = None) -> FastMCP:
    """The FastMCP server with every catalog tool registered (read-only)."""
    mcp = FastMCP("track_me", instructions=INSTRUCTIONS, auth=auth)

    @mcp.tool(name="catalog_overview", annotations=_READ_ONLY)
    def _catalog_overview() -> dict:
        """Catalog totals, the date span of all photos, today's date, and photo
        counts + first/last date per country. Call this first."""
        with _open_db() as db:
            return catalog_overview(db)

    @mcp.tool(name="find_places", annotations=_READ_ONLY)
    def _find_places(
        query: Annotated[str, Field(description="Part of a city, region or address")],
        limit: Annotated[int, Field(ge=1, le=MAX_GROUPS)] = 25,
    ) -> dict:
        """Look up stored place names matching text, with photo counts and date
        spans. Use it to get the exact city spelling for other tools."""
        with _open_db() as db:
            return find_places(db, query, limit)

    @mcp.tool(name="find_photos", annotations=_READ_ONLY)
    def _find_photos(
        start_date: OptDate = None,
        end_date: OptDate = None,
        country: OptCountry = None,
        city: Annotated[str | None, Field(description="Exact stored city name")] = None,
        near_lat: Annotated[float | None, Field(description="Latitude to search near")] = None,
        near_lng: Annotated[float | None, Field(description="Longitude to search near")] = None,
        radius_km: Annotated[float, Field(gt=0, description="Radius for near_*")] = 5.0,
        limit: Annotated[int, Field(ge=1, le=MAX_PHOTOS)] = 50,
    ) -> dict:
        """Photos matching date / country / city / proximity filters. Returns the
        total match count and up to `limit` photos sampled evenly over time, each
        with local time, coordinates, place, Google Photos link and Maps link."""
        with _open_db() as db:
            return find_photos(
                db,
                start_date=start_date,
                end_date=end_date,
                country=country,
                city=city,
                near_lat=near_lat,
                near_lng=near_lng,
                radius_km=radius_km,
                limit=limit,
            )

    @mcp.tool(name="get_trips", annotations=_READ_ONLY)
    def _get_trips(
        start_date: DateStr,
        end_date: DateStr,
        level: Literal["country", "city"] = "country",
        country: OptCountry = None,
        merge_km: Annotated[
            float, Field(gt=0, description="City level: merge photos within this distance")
        ] = 50.0,
        min_hours: Annotated[
            int, Field(ge=0, description="Absorb blips shorter than this between equal stays")
        ] = 24,
        travel_only: Annotated[
            bool,
            Field(
                description="Only stays away from home (is_travel true); no-home periods count"
            ),
        ] = False,
    ) -> dict:
        """The travel timeline for a window: chronological, non-overlapping stays
        (label, from/to dates, centroid, photo count, sample photo link, home, is_travel).
        home is where the user lived at the time; it can be null, because for some
        periods the user had no home (roaming) — then is_travel is always true.
        is_travel is false for stays within 100 miles of home. Use level="country"
        for trips abroad, "city" for an itinerary; travel_only=true to hide time
        at home."""
        with _open_db() as db:
            return get_trips(
                db,
                start_date=start_date,
                end_date=end_date,
                level=level,
                country=country,
                merge_km=merge_km,
                min_hours=min_hours,
                travel_only=travel_only,
            )

    @mcp.tool(name="place_stats", annotations=_READ_ONLY)
    def _place_stats(
        start_date: OptDate = None,
        end_date: OptDate = None,
        group_by: Literal["country", "city"] = "country",
        country: OptCountry = None,
        limit: Annotated[int, Field(ge=1, le=MAX_GROUPS)] = 50,
    ) -> dict:
        """Rank countries or cities by photo count, with days-with-photos and the
        first/last date seen. Answers "where did I go most" questions."""
        with _open_db() as db:
            return place_stats(
                db,
                start_date=start_date,
                end_date=end_date,
                group_by=group_by,
                country=country,
                limit=limit,
            )

    @mcp.tool(name="busiest_days", annotations=_READ_ONLY)
    def _busiest_days(
        start_date: DateStr,
        end_date: DateStr,
        limit: Annotated[int, Field(ge=1, le=100)] = 10,
    ) -> dict:
        """The days with the most photos in a window, with their top cities."""
        with _open_db() as db:
            return busiest_days(db, start_date=start_date, end_date=end_date, limit=limit)

    return mcp


def run(*, host: str, port: int, no_auth: bool = False, stdio: bool = False) -> None:
    """Serve over stdio (local clients) or streamable HTTP at ``/mcp``."""
    fetch_db()
    _require_db()
    if stdio:
        create_server().run(transport="stdio", show_banner=False)
        return
    if no_auth and host not in _LOOPBACK_HOSTS:
        raise SystemExit("--no-auth is only allowed on a loopback host")
    auth = None if no_auth else build_auth()
    # Stateless: every request stands alone, so Cloud Run can restart or scale
    # the container without breaking a client's session.
    create_server(auth).run(
        transport="http", host=host, port=port, stateless_http=True, show_banner=False
    )
