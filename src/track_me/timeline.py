"""Build travel timelines from the photo catalog (Django-free).

Reads the media/place schema via db.py and applies the recipe documented in
AGENTS.md: pull located photos in time order -> segment on label change -> smooth
sub-day blips -> centroid per run. Country labels come from ``place.country_code``;
city-tier labels come from :func:`city_label` — the fine ``place.city``, except a
few metros (Tokyo, Hanoi) collapse their wards/districts onto ``place.admin1``. All
Google-derived at geocode time, so no query-time reverse-geocoding is needed.

"""

from __future__ import annotations

import math
from collections import Counter
from datetime import timedelta

from track_me import config
from track_me.db import HOME_SEED, Database, Home, from_iso, home_on


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
                "admin1": r["admin1"],
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


# admin1 values that ARE effectively the city, so their sub-localities collapse
# onto them instead of fragmenting the map. Google returns each of Tokyo's 23
# special wards (Chiyoda/Chuo/Taito City …) and each of Hanoi's districts as its
# own `locality`, yet they share one admin1 ("Tokyo" / "Hà Nội"). Keyed by
# (country_code, admin1); extend as you hit more KL/HCMC-style metros.
METRO_AS_CITY = {
    ("JP", "Tokyo"),
    ("VN", "Hà Nội"),
}


def city_label(p: dict) -> str | None:
    """The city-tier label for a point.

    Default to the fine ``place.city`` (Nagoya, Osaka, Sapporo …). ``admin1`` is a
    prefecture/region and is too coarse for most places — it would show "Aichi"
    for Nagoya (in this catalog admin1 != city ~6× as often as it matches). Only
    the handful of metros in :data:`METRO_AS_CITY`, whose real "city" is itself an
    admin1 unit and whose wards/districts would otherwise fragment, collapse to
    ``admin1``. Fall back to ``admin1`` only when the fine city is missing.
    """
    admin1 = (p.get("admin1") or "").strip()
    if (p.get("cc"), admin1) in METRO_AS_CITY:
        return admin1
    return p.get("city") or admin1 or None


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
def haversine_km(a_lat, a_lng, b_lat, b_lng) -> float:
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def city_stays(points: list[dict], *, merge_km: float = 50.0, min_hours: int = 24) -> list[dict]:
    """Cluster consecutive photos within merge_km of a running centroid into one
    stay, labelled by the most common city_label (admin1, city fallback) in the
    cluster, then smooth sub-day blips."""
    labelled = [p for p in points if city_label(p)]
    if not labelled:
        return []

    clusters: list[list[dict]] = [[labelled[0]]]
    cen_lat, cen_lng, n = labelled[0]["lat"], labelled[0]["lng"], 1
    for p in labelled[1:]:
        if haversine_km(cen_lat, cen_lng, p["lat"], p["lng"]) <= merge_km:
            clusters[-1].append(p)
            n += 1
            cen_lat += (p["lat"] - cen_lat) / n
            cen_lng += (p["lng"] - cen_lng) / n
        else:
            clusters.append([p])
            cen_lat, cen_lng, n = p["lat"], p["lng"], 1

    for c in clusters:
        label = Counter(city_label(q) for q in c).most_common(1)[0][0]
        for p in c:
            p["_label"] = label
    runs = _smooth(
        _segment([p for c in clusters for p in c], key=lambda p: p["_label"]), min_hours
    )
    return [_stay_from_run(r["pts"], r["key"]) for r in runs]


def tag_travel(stays: list[dict], homes: list[Home] | tuple[Home, ...]) -> list[dict]:
    """Set ``home`` and ``is_travel`` on each stay (in place).

    ``home`` is the label of the home active on the stay's first day, or ``None``
    (no home covers that day). ``is_travel`` is true when the stay's
    centroid is farther than the home's ``radius_km`` from that home, and always true
    when there is no home. A stay is judged as a whole by its start date (it is not
    split at a move)."""
    for s in stays:
        home = home_on(homes, s["from"])
        s["home"] = home.label if home else None
        s["is_travel"] = (
            home is None
            or haversine_km(s["lat"], s["lng"], home.latitude, home.longitude) > home.radius_km
        )
    return stays


def preview(stays: list[dict]) -> str:
    """Human-readable table for showing a draft before writing."""
    lines = [f"{len(stays)} stays:"]
    for i, s in enumerate(stays, 1):
        rng = s["from"] if s["from"] == s["to"] else f"{s['from']}..{s['to']}"
        tag = "" if s.get("is_travel", True) else "  [home]"
        lines.append(f"  {i:>2}. {rng:<24} {s['label']:<28} ({s['photo_count']} photos){tag}")
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
    travel_only: bool = False,
) -> list[dict]:
    """Convenience: load + segment at the requested granularity, tagging each stay
    ``is_travel`` against the home periods (``travel_only`` drops the rest).

    Pass ``points`` (from :func:`load_points`) to reuse an already-loaded set
    instead of querying again — the CLI does this so it can also embed them."""
    if points is None:
        points = load_points(start, end, db=db, region=region)
    if level == "city":
        stays = city_stays(points, merge_km=merge_km, min_hours=min_hours)
    else:
        stays = country_stays(points, min_hours=min_hours)
    tag_travel(stays, db.homes() if db is not None else HOME_SEED)
    return [s for s in stays if s["is_travel"]] if travel_only else stays
