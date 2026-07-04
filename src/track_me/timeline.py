"""Build travel timelines from the photo catalog (Django-free).

Reads the media/place schema via db.py and applies the recipe documented in
CLAUDE.md: pull located photos in time order -> segment on label change -> smooth
sub-day blips -> centroid per run. Country labels come from ``place.country_code``;
city labels from the stored ``place.city`` (Google-derived at geocode time), so no
query-time reverse-geocoding is needed.

The output document matches the viewer/skill schema (stays + prompts + metadata).
"""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import timedelta
from pathlib import Path

from track_me import config
from track_me.db import Database, from_iso, now_utc


# --------------------------------------------------------------------------- #
# 1. Load                                                                      #
# --------------------------------------------------------------------------- #
def load_points(
    start: str,
    end: str,
    *,
    db: Database | None = None,
    region: list[str] | None = None,
) -> list[dict]:
    """Located photos with taken_at in [start, end), joined to their place.

    `start`/`end` are ISO date strings (compared against the UTC taken_at).
    `region` optionally restricts to a list of ISO country codes.
    """
    db = db or Database(config.DB_PATH)
    region_set = {c.upper() for c in region} if region else None
    points: list[dict] = []
    for r in db.located_with_place():  # already ordered by taken_at
        ta = r["taken_at"]
        if ta is None or not (start <= ta < end):
            continue
        if region_set is not None and r["country_code"] not in region_set:
            continue
        points.append(
            {
                "t": from_iso(ta),
                "local_date": r["local_date"] or (ta[:10] if ta else None),
                "lat": r["latitude"],
                "lng": r["longitude"],
                "cc": r["country_code"],
                "city": r["city"],
                "url": r["google_photos_url"],
            }
        )
    return points


# --------------------------------------------------------------------------- #
# 2. Segment + smooth (generic)                                               #
# --------------------------------------------------------------------------- #
def _segment(points: list[dict], key) -> list[dict]:
    runs: list[dict] = []
    for p in points:
        k = key(p)
        if runs and runs[-1]["key"] == k:
            runs[-1]["pts"].append(p)
        else:
            runs.append({"key": k, "pts": [p]})
    return runs


def _smooth(runs: list[dict], min_hours: int = 24) -> list[dict]:
    """Absorb any run < min_hours bracketed by the same key on both sides,
    then re-coalesce adjacent same-key runs. Repeat to a fixed point."""
    changed = True
    while changed:
        changed = False
        for i in range(1, len(runs) - 1):
            span = runs[i]["pts"][-1]["t"] - runs[i]["pts"][0]["t"]
            if span < timedelta(hours=min_hours) and runs[i - 1]["key"] == runs[i + 1]["key"]:
                runs[i - 1]["pts"] += runs[i]["pts"] + runs[i + 1]["pts"]
                del runs[i : i + 2]
                changed = True
                break
    merged: list[dict] = []
    for r in runs:
        if merged and merged[-1]["key"] == r["key"]:
            merged[-1]["pts"] += r["pts"]
        else:
            merged.append(r)
    return merged


def _stay_from_run(pts: list[dict], label: str) -> dict:
    lat = sum(p["lat"] for p in pts) / len(pts)
    lng = sum(p["lng"] for p in pts) / len(pts)
    sample = next((p["url"] for p in pts if p["url"]), None)
    return {
        "label": label,
        "from": pts[0]["local_date"],
        "to": pts[-1]["local_date"],
        "lat": round(lat, 5),
        "lng": round(lng, 5),
        "photo_count": len(pts),
        "sample_url": sample,
    }


# --------------------------------------------------------------------------- #
# 3a. Country-level                                                           #
# --------------------------------------------------------------------------- #
def country_stays(points: list[dict], *, min_hours: int = 24) -> list[dict]:
    labelled = [p for p in points if p["cc"]]
    runs = _smooth(_segment(labelled, key=lambda p: p["cc"]), min_hours)
    return [_stay_from_run(r["pts"], r["key"]) for r in runs]


# --------------------------------------------------------------------------- #
# 3b. City-level (stored place.city + proximity clustering for metros)        #
# --------------------------------------------------------------------------- #
def _haversine_km(a_lat, a_lng, b_lat, b_lng) -> float:
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def city_stays(points: list[dict], *, merge_km: float = 50.0, min_hours: int = 24) -> list[dict]:
    """Cluster consecutive photos within merge_km of a running centroid into one
    stay, labelled by the most common stored place.city in the cluster, then
    smooth sub-day blips."""
    labelled = [p for p in points if p["city"]]
    if not labelled:
        return []

    clusters: list[list[dict]] = [[labelled[0]]]
    cen_lat, cen_lng, n = labelled[0]["lat"], labelled[0]["lng"], 1
    for p in labelled[1:]:
        if _haversine_km(cen_lat, cen_lng, p["lat"], p["lng"]) <= merge_km:
            clusters[-1].append(p)
            n += 1
            cen_lat += (p["lat"] - cen_lat) / n
            cen_lng += (p["lng"] - cen_lng) / n
        else:
            clusters.append([p])
            cen_lat, cen_lng, n = p["lat"], p["lng"], 1

    for c in clusters:
        label = Counter(q["city"] for q in c).most_common(1)[0][0]
        for p in c:
            p["_label"] = label
    runs = _smooth(
        _segment([p for c in clusters for p in c], key=lambda p: p["_label"]), min_hours
    )
    return [_stay_from_run(r["pts"], r["key"]) for r in runs]


# --------------------------------------------------------------------------- #
# 4. Points payload (for the interactive viewer)                              #
# --------------------------------------------------------------------------- #
# Compact, self-describing columnar rows so the viewer can filter by time and
# re-cluster at any granularity (country / city / neighborhood) client-side.
POINT_FIELDS = ["t", "lat", "lng", "cc", "city", "photo_id"]
_PHOTO_URL_PREFIX = "https://photos.google.com/photo/"


def _photo_id(url: str | None) -> str | None:
    """Strip the common Google Photos prefix to keep the payload small.

    Non-matching URLs are kept whole; the viewer treats any value starting with
    ``http`` as a full URL and otherwise re-prepends the prefix."""
    if not url:
        return None
    return url[len(_PHOTO_URL_PREFIX) :] if url.startswith(_PHOTO_URL_PREFIX) else url


def points_payload(points: list[dict]) -> list[list]:
    """Turn loaded ``load_points`` rows into compact columnar POINT_FIELDS rows."""
    rows: list[list] = []
    for p in points:
        if p["lat"] is None or p["lng"] is None:
            continue
        t = p["t"]
        # minute resolution is plenty for a timeline and trims the payload
        ts = t.strftime("%Y-%m-%dT%H:%M") if t is not None else (p["local_date"] or "")
        rows.append(
            [
                ts,
                round(p["lat"], 5),
                round(p["lng"], 5),
                p["cc"],
                p["city"],
                _photo_id(p["url"]),
            ]
        )
    return rows


# --------------------------------------------------------------------------- #
# 5. Document + persist                                                        #
# --------------------------------------------------------------------------- #
def to_document(
    stays: list[dict],
    *,
    timeline_id: str,
    title: str,
    prompts: list[str],
    points: list[dict] | None = None,
) -> dict:
    doc = {
        "id": timeline_id,
        "title": title,
        "prompts": prompts,
        "generated_at": now_utc().replace(microsecond=0).isoformat(),
        "stays": stays,
    }
    if points is not None:
        doc["photo_url_prefix"] = _PHOTO_URL_PREFIX
        doc["point_fields"] = POINT_FIELDS
        doc["points"] = points_payload(points)
    return doc


def write_timeline(doc: dict) -> Path:
    """Persist to userdata/timelines/<id>.json. Call ONLY after the user confirms."""
    config.ensure_dirs()
    out = config.TIMELINES_DIR / f"{doc['id']}.json"
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    return out


def preview(stays: list[dict]) -> str:
    """Human-readable table for showing a draft before writing."""
    lines = [f"{len(stays)} stays:"]
    for i, s in enumerate(stays, 1):
        rng = s["from"] if s["from"] == s["to"] else f"{s['from']}..{s['to']}"
        lines.append(f"  {i:>2}. {rng:<24} {s['label']:<28} ({s['photo_count']} photos)")
    return "\n".join(lines)


def build_stays(
    start: str,
    end: str,
    *,
    level: str = "country",
    region: list[str] | None = None,
    merge_km: float = 50.0,
    min_hours: int = 24,
    db: Database | None = None,
    points: list[dict] | None = None,
) -> list[dict]:
    """Convenience: load + segment at the requested granularity.

    Pass ``points`` (from :func:`load_points`) to reuse an already-loaded set
    instead of querying again — the CLI does this so it can also embed them."""
    if points is None:
        points = load_points(start, end, db=db, region=region)
    if level == "city":
        return city_stays(points, merge_km=merge_km, min_hours=min_hours)
    return country_stays(points, min_hours=min_hours)
