"""Geocoder tests with a mocked Google Maps client (no network)."""

import pytest

from library.models import LocationSource, MediaItem
from places.geocode import Geocoder


def _fake_reverse_geocode(latlng, language=None):
    """Encode the queried cell-center into the address so cells are distinguishable."""
    lat, lon = latlng
    return [
        {
            "formatted_address": f"Place @ {lat:.2f},{lon:.2f}",
            "address_components": [{"types": ["country"], "short_name": "SG"}],
        }
    ]


def _make_item(key, lat, lon):
    item = MediaItem(dedupe_key=key, file_name=f"{key}.jpg")
    item.set_location(lat, lon, source=LocationSource.EXIF_GPS)
    item.save()
    return item


@pytest.fixture
def geocoder():
    g = Geocoder(api_key="AIzaSyTESTFAKEKEY0000000000000000000000")
    g.client.reverse_geocode = _fake_reverse_geocode  # ty: ignore[unresolved-attribute]
    return g


@pytest.mark.django_db
def test_h3_batching_reduces_calls(geocoder):
    # Two items ~5 m apart (same H3 cell) + one far away (Tokyo) -> 2 cells.
    _make_item("a", 1.30000, 103.80000)
    _make_item("b", 1.30005, 103.80005)
    _make_item("c", 35.6800, 139.6900)

    stats = geocoder.geocode_items(resolution=9)

    assert stats.total_items == 3
    assert stats.processed == 3
    assert stats.api_calls == 2  # batched: one call per cell, not per item


@pytest.mark.django_db
def test_applies_label_and_country(geocoder):
    _make_item("x", 1.3, 103.8)
    geocoder.geocode_items(resolution=9)
    item = MediaItem.objects.get(dedupe_key="x")
    assert item.place_label.startswith("Place @")
    assert item.country_code == "SG"
    assert item.geocoded_at is not None


@pytest.mark.django_db
def test_items_in_same_cell_share_label(geocoder):
    _make_item("a", 1.30000, 103.80000)
    _make_item("b", 1.30005, 103.80005)
    geocoder.geocode_items(resolution=9)
    a = MediaItem.objects.get(dedupe_key="a")
    b = MediaItem.objects.get(dedupe_key="b")
    assert a.place_label == b.place_label


@pytest.mark.django_db
def test_skips_already_geocoded(geocoder):
    _make_item("x", 1.3, 103.8)
    geocoder.geocode_items(resolution=9)
    # Second pass: nothing left to do unless recalculate.
    stats = geocoder.geocode_items(resolution=9)
    assert stats.total_items == 0
    assert stats.api_calls == 0


@pytest.mark.django_db
def test_recalculate_reprocesses(geocoder):
    _make_item("x", 1.3, 103.8)
    geocoder.geocode_items(resolution=9)
    stats = geocoder.geocode_items(resolution=9, recalculate=True)
    assert stats.total_items == 1
    assert stats.api_calls == 1


@pytest.mark.django_db
def test_unlocated_items_ignored(geocoder):
    # An item with no coords must not be geocoded.
    MediaItem.objects.create(dedupe_key="noloc", file_name="noloc.jpg")
    stats = geocoder.geocode_items(resolution=9)
    assert stats.total_items == 0


def test_reverse_geocode_parses_country(geocoder):
    info = geocoder.reverse_geocode(1.3, 103.8)
    assert info is not None
    assert info["country_code"] == "SG"
    assert "Place @" in info["formatted_address"]
