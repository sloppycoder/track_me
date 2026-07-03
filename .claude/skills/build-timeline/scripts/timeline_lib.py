"""Reusable building blocks for constructing a travel timeline from the photo DB.

Encodes the recipe from CLAUDE.md ("Answering travel/trip questions") as composable
functions so the agent doesn't re-derive it each turn:

    load_points()      pull located photos in a window, in chronological order
    country_stays()    segment + smooth by country_code
    city_stays()       reverse-geocode + proximity-cluster into city stays
    to_document()      wrap stays in the viewer's JSON schema
    write_timeline()   persist to userdata/timelines/<id>.json  (only when user confirms)

Ordering uses taken_at (UTC); from/to dates use the photo's LOCAL day (via the
stored `timezone`) so cross-timezone travel aligns to the right calendar day.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# repo root = four levels up from this file (.claude/skills/build-timeline/scripts/)
REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DB = REPO_ROOT / "data" / "track_me.db"


# --------------------------------------------------------------------------- #
# 1. Load                                                                      #
# --------------------------------------------------------------------------- #
def load_points(
    start: str,
    end: str,
    *,
    db: Path | str = DEFAULT_DB,
    region: list[str] | None = None,
) -> list[dict]:
    """Located photos with taken_at in [start, end), chronological.

    `start`/`end` are ISO date strings (compared against the UTC taken_at).
    `region` optionally restricts to a list of ISO country codes.
    Each point: {t (UTC aware), local_date (str), lat, lng, cc, url, tz}.
    """
    con = sqlite3.connect(str(db))
    sql = (
        "SELECT taken_at, timezone, latitude, longitude, country_code, google_photos_url "
        "FROM media_item "
        "WHERE latitude IS NOT NULL AND taken_at >= ? AND taken_at < ? "
    )
    params: list = [start, end]
    if region:
        sql += f"AND country_code IN ({','.join('?' * len(region))}) "
        params += [c.upper() for c in region]
    sql += "ORDER BY taken_at"

    points: list[dict] = []
    for taken_at, tz, lat, lng, cc, url in con.execute(sql, params):
        t = datetime.fromisoformat(taken_at).replace(tzinfo=timezone.utc)
        points.append(
            {
                "t": t,
                "local_date": _local_date(t, tz),
                "lat": float(lat),
                "lng": float(lng),
                "cc": cc,
                "url": url,
                "tz": tz,
            }
        )
    con.close()
    return points


def _local_date(t: datetime, tz: str | None) -> str:
    if tz:
        try:
            return t.astimezone(ZoneInfo(tz)).date().isoformat()
        except Exception:
            pass
    return t.date().isoformat()


# --------------------------------------------------------------------------- #
# 2. Segment + smooth (generic)                                               #
# --------------------------------------------------------------------------- #
def _segment(points: list[dict], key) -> list[dict]:
    """Contiguous runs where key(point) is unchanged."""
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
    runs = _smooth(_segment(points, key=lambda p: p["cc"]), min_hours)
    return [_stay_from_run(r["pts"], r["key"]) for r in runs]


# --------------------------------------------------------------------------- #
# 3b. City-level (offline reverse geocode + proximity clustering)            #
# --------------------------------------------------------------------------- #
def _haversine_km(a_lat, a_lng, b_lat, b_lng) -> float:
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def city_stays(points: list[dict], *, merge_km: float = 50.0, min_hours: int = 24) -> list[dict]:
    """Proximity-first clustering (recipe step 5): walk points in order,
    keep extending the current stay while the next photo is within merge_km of
    its running centroid; otherwise start a new stay. Label each stay by its
    most common GeoNames city. Then smooth sub-day blips."""
    if not points:
        return []
    import reverse_geocoder as rg  # heavy import; only when city-level is used

    hits = rg.search([(p["lat"], p["lng"]) for p in points])
    for p, h in zip(points, hits):
        p["_city"] = f"{h['name']}, {h['admin1']} ({h['cc']})"

    # proximity clusters with running centroid
    clusters: list[list[dict]] = [[points[0]]]
    cen_lat, cen_lng, n = points[0]["lat"], points[0]["lng"], 1
    for p in points[1:]:
        if _haversine_km(cen_lat, cen_lng, p["lat"], p["lng"]) <= merge_km:
            clusters[-1].append(p)
            n += 1
            cen_lat += (p["lat"] - cen_lat) / n
            cen_lng += (p["lng"] - cen_lng) / n
        else:
            clusters.append([p])
            cen_lat, cen_lng, n = p["lat"], p["lng"], 1

    # tag each cluster with its dominant city, then smooth on that label
    for c in clusters:
        for p in c:
            p["_label"] = Counter(q["_city"] for q in c).most_common(1)[0][0]
    runs = _smooth(
        _segment([p for c in clusters for p in c], key=lambda p: p["_label"]), min_hours
    )
    return [_stay_from_run(r["pts"], r["key"]) for r in runs]


# --------------------------------------------------------------------------- #
# 4. Document + persist                                                       #
# --------------------------------------------------------------------------- #
def to_document(stays: list[dict], *, timeline_id: str, title: str, prompts: list[str]) -> dict:
    return {
        "id": timeline_id,
        "title": title,
        "prompts": prompts,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "stays": stays,
    }


def write_timeline(doc: dict, *, repo_root: Path | str = REPO_ROOT) -> Path:
    """Persist to userdata/timelines/<id>.json. Call ONLY after the user confirms."""
    out = Path(repo_root) / "userdata" / "timelines" / f"{doc['id']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
    return out


def preview(stays: list[dict]) -> str:
    """Human-readable table for showing a draft before writing."""
    lines = [f"{len(stays)} stays:"]
    for i, s in enumerate(stays, 1):
        rng = s["from"] if s["from"] == s["to"] else f"{s['from']}..{s['to']}"
        lines.append(f"  {i:>2}. {rng:<24} {s['label']:<28} ({s['photo_count']} photos)")
    return "\n".join(lines)
