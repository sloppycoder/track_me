"""Export located media as timestamped points for timeline tools.

GeoPulse and Dawarich both import GPX (and GeoJSON). Each located photo becomes
one timestamped point, ordered by capture time, so their stay/trip detection can
run over your photo-derived "track".
"""

from __future__ import annotations

import json
from datetime import timezone as dt_timezone
from typing import Iterable

from track_me.db import Database, Media


def _utc(dt) -> str:
    return dt.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def media_to_gpx(items: Iterable[Media]) -> str:
    """Render items as a GPX 1.1 track of timestamped points."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="track_me" xmlns="http://www.topografix.com/GPX/1/1">',
        "  <trk><name>track_me photo locations</name><trkseg>",
    ]
    for it in items:
        lines.append(
            f'    <trkpt lat="{it.latitude}" lon="{it.longitude}">'
            f"<time>{_utc(it.taken_at)}</time></trkpt>"
        )
    lines += ["  </trkseg></trk>", "</gpx>", ""]
    return "\n".join(lines)


def media_to_geojson(items: Iterable[Media]) -> str:
    """Render items as a GeoJSON FeatureCollection of timestamped points."""
    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                # GeoJSON is [longitude, latitude]; coords are REAL (already float)
                "coordinates": [it.longitude, it.latitude],
            },
            "properties": {
                "time": _utc(it.taken_at),
                "id": it.id,
                "name": it.file_name,
            },
        }
        for it in items
    ]
    return json.dumps({"type": "FeatureCollection", "features": features}, indent=2)


def located_items(db: Database, year: int | None = None) -> list[Media]:
    """Located items with a timestamp, ordered by absolute time.

    ``year`` filters on the photo's LOCAL year (via ``local_date``) so a photo
    taken near midnight abroad lands in the correct year.
    """
    return db.iter_located(year=year)
