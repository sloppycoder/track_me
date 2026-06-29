"""Tests for GPX / GeoJSON export of located media."""

import json
from datetime import datetime
from datetime import timezone as dt_timezone

import pytest

from library.export import located_items, media_to_geojson, media_to_gpx
from library.models import LocationSource, MediaItem


def _item(key, lat, lon, when):
    item = MediaItem(dedupe_key=key, file_name=f"{key}.jpg", taken_at=when)
    item.set_location(lat, lon, source=LocationSource.EXIF_GPS)
    item.save()
    return item


@pytest.fixture
def points(db):
    # Out of order on purpose; export must sort by time.
    _item("b", 35.68, 139.69, datetime(2025, 6, 2, tzinfo=dt_timezone.utc))
    _item("a", 1.30, 103.80, datetime(2025, 1, 1, tzinfo=dt_timezone.utc))
    _item("c", 48.85, 2.35, datetime(2024, 3, 3, tzinfo=dt_timezone.utc))
    # Excluded: no location, and no timestamp.
    MediaItem.objects.create(
        dedupe_key="noloc",
        file_name="noloc.jpg",
        taken_at=datetime(2025, 5, 5, tzinfo=dt_timezone.utc),
    )
    nt = MediaItem(dedupe_key="notime", file_name="notime.jpg")
    nt.set_location(10.0, 10.0, source=LocationSource.EXIF_GPS)
    nt.save()


@pytest.mark.django_db
def test_located_items_filters_and_orders(points):
    items = list(located_items())
    assert [i.dedupe_key for i in items] == ["c", "a", "b"]  # time-sorted


@pytest.mark.django_db
def test_gpx_output(points):
    gpx = media_to_gpx(located_items())
    assert gpx.startswith("<?xml")
    assert gpx.count("<trkpt") == 3
    # Earliest point (2024) appears before the 2025 ones.
    assert gpx.index("2024-03-03") < gpx.index("2025-01-01") < gpx.index("2025-06-02")
    assert 'lat="1.300000" lon="103.800000"' in gpx
    assert "2025-01-01T00:00:00Z" in gpx


@pytest.mark.django_db
def test_geojson_output(points):
    data = json.loads(media_to_geojson(located_items()))
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == 3
    first = data["features"][0]
    # GeoJSON is [lon, lat]; first (time-sorted) is Paris (2024-03-03).
    assert first["geometry"]["coordinates"] == [2.35, 48.85]
    assert first["properties"]["time"] == "2024-03-03T00:00:00Z"


@pytest.mark.django_db
def test_year_filter(points):
    assert located_items(year=2025).count() == 2
    assert located_items(year=2024).count() == 1
