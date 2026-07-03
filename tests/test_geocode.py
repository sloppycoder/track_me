"""Geocoder tests with a mocked Google Maps client (no network)."""

import pytest

from places.geocode import Geocoder, derive_place, estimate_calls
from track_me.db import Database, LocationSource, Media, Place


def _fake_reverse_geocode(latlng, language=None):
    """Encode the queried cell-center into the address so cells are distinguishable."""
    lat, lon = latlng
    return [
        {
            "formatted_address": f"Place @ {lat:.2f},{lon:.2f}",
            "address_components": [
                {"types": ["locality"], "long_name": f"City_{lat:.1f}"},
                {"types": ["administrative_area_level_1"], "long_name": "Region"},
                {"types": ["country"], "short_name": "SG"},
            ],
        }
    ]


class _FakeClient:
    reverse_geocode = staticmethod(_fake_reverse_geocode)


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "t.db")
    database.init_schema()
    yield database
    database.close()


@pytest.fixture
def geocoder(db):
    g = Geocoder(db=db, api_key="AIzaSyTESTFAKEKEY0000000000000000000000")
    g._client = _FakeClient()  # bypass the real googlemaps client
    return g


def _make_item(db: Database, key: str, lat: float, lon: float) -> None:
    item = Media(dedupe_key=key, file_name=f"{key}.jpg")
    item.set_location(lat, lon, source=LocationSource.EXIF_GPS)
    db.upsert_media(item)


def _place_of(db: Database, key: str) -> Place | None:
    m = db.get_media_by_dedupe_key(key)
    return db.get_place(m.geo_cell) if m and m.geo_cell else None


def test_h3_batching_reduces_calls(db, geocoder):
    # Two items ~5 m apart (same H3 cell) + one far away (Tokyo) -> 2 cells.
    _make_item(db, "a", 1.30000, 103.80000)
    _make_item(db, "b", 1.30005, 103.80005)
    _make_item(db, "c", 35.6800, 139.6900)

    stats = geocoder.geocode_items(resolution=9)

    assert stats.total_items == 3
    assert stats.processed == 3
    assert stats.api_calls == 2  # batched: one call per cell, not per item


def test_applies_place_and_country(db, geocoder):
    _make_item(db, "x", 1.3, 103.8)
    geocoder.geocode_items(resolution=9)
    place = _place_of(db, "x")
    assert place is not None
    assert place.formatted_address and place.formatted_address.startswith("Place @")
    assert place.country_code == "SG"
    assert place.city and place.city.startswith("City_")  # derived at fetch time
    assert place.geocoded_at is not None


def test_items_in_same_cell_share_place(db, geocoder):
    _make_item(db, "a", 1.30000, 103.80000)
    _make_item(db, "b", 1.30005, 103.80005)
    geocoder.geocode_items(resolution=9)
    a = db.get_media_by_dedupe_key("a")
    b = db.get_media_by_dedupe_key("b")
    assert a.geo_cell == b.geo_cell  # same place row


def test_skips_already_geocoded(db, geocoder):
    _make_item(db, "x", 1.3, 103.8)
    geocoder.geocode_items(resolution=9)
    # Second pass: geo_cell is set, so nothing is pending.
    stats = geocoder.geocode_items(resolution=9)
    assert stats.total_items == 0
    assert stats.api_calls == 0


def test_recalculate_reprocesses(db, geocoder):
    _make_item(db, "x", 1.3, 103.8)
    geocoder.geocode_items(resolution=9)
    stats = geocoder.geocode_items(resolution=9, recalculate=True)
    assert stats.total_items == 1
    assert stats.api_calls == 1


def test_max_api_calls_caps_fetches(db, geocoder):
    _make_item(db, "a", 1.30, 103.80)
    _make_item(db, "c", 35.68, 139.69)  # different cell
    stats = geocoder.geocode_items(resolution=9, max_api_calls=1)
    assert stats.api_calls == 1  # stopped after the cap; second cell left pending


def test_unlocated_items_ignored(db, geocoder):
    db.upsert_media(Media(dedupe_key="noloc", file_name="noloc.jpg"))
    stats = geocoder.geocode_items(resolution=9)
    assert stats.total_items == 0


def test_estimate_calls_no_api(db):
    _make_item(db, "a", 1.30000, 103.80000)
    _make_item(db, "b", 1.30005, 103.80005)  # ~8 m from a -> same cell here
    _make_item(db, "c", 35.6800, 139.6900)  # Tokyo, separate
    total, counts = estimate_calls(db, [6, 9, 10, 11])
    assert total == 3
    assert counts[9] == 2  # a+b share a cell, c separate
    vals = [counts[r] for r in (6, 9, 10, 11)]
    assert vals == sorted(vals)  # finer resolution -> same or more cells


def test_reverse_geocode_parses_country(geocoder):
    info = geocoder.reverse_geocode(1.3, 103.8)
    assert info is not None
    assert info["country_code"] == "SG"
    assert "Place @" in info["formatted_address"]


def test_derive_only_recomputes_offline(db, geocoder):
    # Seed a place with only a raw response (no city yet), as a fetch would.
    db.upsert_place(
        Place(
            h3_cell="cell-1",
            geocode_raw=[
                {"types": ["postal_town"], "long_name": "Cambridge"},
                {"types": ["administrative_area_level_1"], "long_name": "England"},
            ],
        )
    )
    n = geocoder.derive_all(redo=True)
    assert n == 1
    p = db.get_place("cell-1")
    assert p.city == "Cambridge"  # postal_town fallback (no locality present)
    assert p.admin1 == "England"


@pytest.mark.parametrize(
    "components, expected_city",
    [
        ([{"types": ["locality"], "long_name": "Paris"}], "Paris"),
        (  # locality wins over lower-priority types
            [
                {"types": ["administrative_area_level_2"], "long_name": "County"},
                {"types": ["locality"], "long_name": "Lyon"},
            ],
            "Lyon",
        ),
        ([{"types": ["postal_town"], "long_name": "Oxford"}], "Oxford"),  # UK
        ([{"types": ["administrative_area_level_3"], "long_name": "Comune"}], "Comune"),  # IT
        ([{"types": ["administrative_area_level_2"], "long_name": "OnlyCounty"}], "OnlyCounty"),
        ([{"types": ["route"], "long_name": "Main St"}], None),  # nothing city-like
    ],
)
def test_derive_place_priority_chain(components, expected_city):
    city, _admin1 = derive_place(components)
    assert city == expected_city
