"""Geocoder tests with a mocked Google Maps client (no network)."""

import pytest

from track_me.db import Database, LocationSource, Media, Place
from track_me.geocode import (
    Geocoder,
    country_code_of,
    derive_place,
    estimate_calls,
    merge_components,
    place_country,
)


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


def test_derive_place_normalizes_chinese_cities_only():
    cn = [
        {"types": ["locality"], "long_name": "Cheng Du Shi"},
        {"types": ["country"], "short_name": "CN"},
    ]
    assert derive_place(cn)[0] == "Chengdu"
    tw = [  # same text outside CN is left alone
        {"types": ["locality"], "long_name": "Cheng Du Shi"},
        {"types": ["country"], "short_name": "TW"},
    ]
    assert derive_place(tw)[0] == "Cheng Du Shi"


def test_derive_all_rewrites_stored_chinese_names(db, geocoder):
    for cell, name in [("c1", "Cheng Du Shi"), ("c2", "Chengdu")]:
        db.upsert_place(
            Place(
                h3_cell=cell,
                city=name,  # as stored by an older derive
                geocode_raw=[
                    {"types": ["locality"], "long_name": name},
                    {"types": ["country"], "short_name": "CN"},
                ],
            )
        )
    geocoder.derive_all(redo=True)
    assert {db.get_place(c).city for c in ("c1", "c2")} == {"Chengdu"}


@pytest.mark.parametrize(
    "district, admin1, expected",
    [
        ("Xu Hui Qu", "Shang Hai Shi", "Shanghai"),
        ("Pudong", "Shanghai", "Shanghai"),
        ("Xuhui District", "Shanghai", "Shanghai"),
        ("Yu Zhong Qu", "Chong Qing Shi", "Chongqing"),
        ("Chao Yang Qu", "Bei Jing Shi", "Beijing"),
        ("Guang Zhou Shi", "Guang Dong Sheng", "Guangzhou"),  # province: city kept
    ],
)
def test_derive_place_rolls_municipality_districts_up(district, admin1, expected):
    components = [
        {"types": ["locality"], "long_name": district},
        {"types": ["administrative_area_level_1"], "long_name": admin1},
        {"types": ["country"], "short_name": "CN"},
    ]
    city, got_admin1 = derive_place(components)
    assert city == expected
    assert got_admin1 == admin1  # admin1 is stored as Google returned it


# --- change log hooks -------------------------------------------------------
def test_derive_all_counts_only_changed_places(db, geocoder):
    from track_me.cli import log_geocode
    from track_me.geocode import GeocodeStats

    db.upsert_place(
        Place(
            h3_cell="cell-9",
            center_lat=1.0,
            center_lng=1.0,
            geocode_raw=[{"types": ["locality"], "long_name": "Lyon"}],
        )
    )
    assert geocoder.derive_all(redo=True) == 1  # city filled in
    assert geocoder.derive_all(redo=True) == 0  # nothing new to derive

    before = len(db.recent_changes(100))
    assert log_geocode(db, GeocodeStats(), derived=0) is None  # no-op run logs nothing
    assert len(db.recent_changes(100)) == before
    assert log_geocode(db, GeocodeStats(processed=5, api_calls=2), derived=1) is not None
    assert db.recent_changes(1)[0]["command"] == "geocode"


# --- multi-result merge + country fallback ----------------------------------
_PLUS_CODE_FIRST = [  # shape of Google's answer in Yosemite: plus code first
    {
        "types": ["plus_code"],
        "formatted_address": "VF76+R8 Yosemite Valley, CA, USA",
        "address_components": [{"types": ["plus_code"], "long_name": "VF76+R8"}],
    },
    {
        "types": ["establishment", "park"],
        "formatted_address": "Yosemite National Park, California, USA",
        "address_components": [
            {"types": ["establishment", "park"], "long_name": "Yosemite National Park"},
            {"types": ["administrative_area_level_1", "political"], "long_name": "California"},
            {"types": ["country", "political"], "long_name": "USA", "short_name": "US"},
        ],
    },
    {
        "types": ["administrative_area_level_2", "political"],
        "formatted_address": "Mariposa County, CA, USA",
        "address_components": [
            {
                "types": ["administrative_area_level_2", "political"],
                "long_name": "Mariposa County",
            },
            {"types": ["administrative_area_level_1", "political"], "long_name": "Calif."},
            {"types": ["country", "political"], "long_name": "Mexico?", "short_name": "MX"},
        ],
    },
]


def test_merge_components_skips_plus_code_and_fills_gaps():
    base, merged = merge_components(_PLUS_CODE_FIRST)
    assert base["formatted_address"].startswith("Yosemite National Park")
    assert country_code_of(merged) == "US"  # first-seen wins, later dupes ignored
    assert derive_place(merged) == ("Mariposa County", "California")


def test_merge_components_all_plus_code_keeps_first():
    only = [_PLUS_CODE_FIRST[0]]
    base, merged = merge_components(only)
    assert base is only[0]
    assert merged == only[0]["address_components"]


def test_reverse_geocode_merges_results(geocoder):
    geocoder._client = type(
        "C", (), {"reverse_geocode": staticmethod(lambda *a, **k: _PLUS_CODE_FIRST)}
    )
    info = geocoder.reverse_geocode(37.865, -119.538)
    assert info["country_code"] == "US"
    assert info["formatted_address"] == "Yosemite National Park, California, USA"


@pytest.mark.parametrize(
    "lat, lng",
    [
        (31.7767, 35.2345),
        (31.7054, 35.2024),
    ],  # Jerusalem (Asia/Jerusalem), Bethlehem (Asia/Hebron)
)
def test_place_country_falls_back_to_israel(lat, lng):
    components = [{"types": ["locality"], "long_name": "X"}]  # Google gave no country
    assert place_country(components, lat, lng) == "IL"


def test_place_country_prefers_google_and_ignores_other_zones():
    google = [{"types": ["country"], "short_name": "PS"}]
    assert place_country(google, 31.7054, 35.2024) == "PS"  # never overrides Google
    assert place_country([], 35.68, 139.69) is None  # Tokyo: no fallback entry
    assert place_country([], None, None) is None


def test_derive_all_fills_missing_country(db, geocoder):
    db.upsert_place(
        Place(
            h3_cell="bethlehem",
            center_lat=31.7054,
            center_lng=35.2024,
            geocode_raw=[{"types": ["route"], "long_name": "Bethlehem-Beit Sahour"}],
        )
    )
    assert geocoder.derive_all(redo=True) == 1
    assert db.get_place("bethlehem").country_code == "IL"
    assert geocoder.derive_all(redo=True) == 0  # stable on re-run


def test_derive_place_translates_city_aliases():
    components = [
        {"types": ["locality", "political"], "long_name": "新加坡"},
        {"types": ["country", "political"], "short_name": "SG"},
    ]
    assert derive_place(components)[0] == "Singapore"
