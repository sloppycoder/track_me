"""Export located media as timestamped points for timeline tools.

GeoPulse and Dawarich both import GPX (and GeoJSON). Each located ``MediaItem``
becomes one timestamped point, ordered by capture time, so their stay/trip
detection can run over your photo-derived "track".
"""

from __future__ import annotations

import json
from datetime import timezone as dt_timezone
from typing import Iterable

from library.models import MediaItem


def _utc(dt) -> str:
    return dt.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def media_to_gpx(items: Iterable[MediaItem]) -> str:
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


def media_to_geojson(items: Iterable[MediaItem]) -> str:
    """Render items as a GeoJSON FeatureCollection of timestamped points."""
    features = [
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                # GeoJSON is [longitude, latitude]
                "coordinates": [float(it.longitude), float(it.latitude)],
            },
            "properties": {
                "time": _utc(it.taken_at),
                "id": it.pk,
                "name": it.file_name,
            },
        }
        for it in items
    ]
    return json.dumps({"type": "FeatureCollection", "features": features}, indent=2)


def located_items(year: int | None = None):
    """Queryset of items that have both a location and a timestamp, time-ordered."""
    qs = MediaItem.objects.filter(
        latitude__isnull=False, longitude__isnull=False, taken_at__isnull=False
    ).order_by("taken_at")
    if year is not None:
        qs = qs.filter(taken_at__year=year)
    return qs
