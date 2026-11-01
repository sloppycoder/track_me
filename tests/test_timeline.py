"""Tests for timeline building over the media/place schema."""

from datetime import datetime
from datetime import timezone as dt_timezone

import pytest

from track_me import timeline as tl
from track_me.db import Database, LocationSource, Media, Place


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "t.db")
    database.init_schema()
    yield database
    database.close()


def _photo(db, key, when, lat, lon, cell):
    m = Media(dedupe_key=key, file_name=f"{key}.jpg", taken_at=when)
    m.set_location(lat, lon, source=LocationSource.EXIF_GPS)
    m.geo_cell = cell
    m.refresh_local_date()
    db.upsert_media(m)


@pytest.fixture
def trip(db):
    # Two places (countries + cities), linked via geo_cell.
    db.upsert_place(Place(h3_cell="cSG", city="Singapore", country_code="SG"))
    db.upsert_place(Place(h3_cell="cTH", city="Bangkok", country_code="TH"))
    d = lambda day: datetime(2019, 6, day, 12, 0, tzinfo=dt_timezone.utc)  # noqa: E731
    _photo(db, "s1", d(1), 1.30, 103.80, "cSG")
    _photo(db, "s2", d(2), 1.31, 103.81, "cSG")
    _photo(db, "t1", d(3), 13.75, 100.50, "cTH")
    _photo(db, "t2", d(4), 13.76, 100.51, "cTH")
    _photo(db, "t3", d(5), 13.77, 100.52, "cTH")
    _photo(db, "s3", d(6), 1.30, 103.80, "cSG")
    return db


def test_country_timeline(trip):
    stays = tl.build_stays("2019-06-01", "2019-07-01", level="country", db=trip)
    assert [s["label"] for s in stays] == ["SG", "TH", "SG"]
    assert stays[0]["from"] == "2019-06-01"
    assert stays[1]["from"] == "2019-06-03"
    assert stays[1]["to"] == "2019-06-05"
    assert stays[2]["photo_count"] == 1


def test_city_timeline_reads_stored_place_city(trip):
    stays = tl.build_stays("2019-06-01", "2019-07-01", level="city", db=trip)
    labels = [s["label"] for s in stays]
    assert labels == ["Singapore", "Bangkok", "Singapore"]


def test_region_filter(trip):
    stays = tl.build_stays("2019-06-01", "2019-07-01", level="country", region=["TH"], db=trip)
    assert [s["label"] for s in stays] == ["TH"]


def test_date_window_excludes_outside(trip):
    stays = tl.build_stays("2019-06-03", "2019-06-06", level="country", db=trip)
    # Only the 3 Thailand days fall in [06-03, 06-06).
    assert [s["label"] for s in stays] == ["TH"]
    assert stays[0]["photo_count"] == 3


# --- home / is_travel -------------------------------------------------------
from track_me.db import HOME_SEED, Home, home_on  # noqa: E402


def test_home_seed_periods_are_ordered_and_disjoint():
    prev_end = None
    for h in HOME_SEED:
        assert h.valid_to is None or h.valid_from <= h.valid_to
        if prev_end:
            assert prev_end < h.valid_from
        prev_end = h.valid_to


def _label(day):
    h = home_on(HOME_SEED, day)
    return h.label if h else None


def test_home_on_boundaries_and_roaming_gap():
    assert _label("1998-04-11") == "San Jose, CA"
    assert _label("2005-10-09") == "San Jose, CA"
    assert _label("2005-10-10") == "Beijing, CN"
    assert _label("2024-04-03") == "Singapore, SG"
    assert _label("2024-04-04") is None  # roaming
    assert _label("2026-11-25") is None
    assert _label("2026-11-26") == "Bellingham, WA"
    assert _label("2027-06-30") == "Bellingham, WA"
    assert _label("2027-07-01") is None
    assert home_on(HOME_SEED, None) is None


def test_init_schema_seeds_home_table(db):
    assert db.homes() == list(HOME_SEED)
    db.init_schema()  # re-runnable, no duplicates
    assert len(db.homes()) == len(HOME_SEED)


def test_tag_travel_radius_and_no_home():
    homes = [Home("Singapore", 1.3521, 103.8198, "2019-01-28", "2024-04-03")]
    stays = [
        {"from": "2019-06-01", "lat": 1.40, "lng": 103.9},  # in SG
        {"from": "2019-06-05", "lat": 1.49, "lng": 103.74},  # Johor Bahru, ~20 km
        {"from": "2019-06-09", "lat": 3.139, "lng": 101.687},  # KL, ~316 km
        {"from": "2024-06-01", "lat": 1.40, "lng": 103.9},  # SG but no home then
    ]
    out = [s["is_travel"] for s in tl.tag_travel(stays, homes)]
    assert out == [False, False, True, True]


def test_build_stays_tags_and_filters(trip):
    # 2019-06: home is Singapore, so SG stays are home and Bangkok is travel.
    stays = tl.build_stays("2019-06-01", "2019-06-30", level="country", db=trip)
    assert {s["label"]: s["is_travel"] for s in stays} == {"SG": False, "TH": True}
    away = tl.build_stays("2019-06-01", "2019-06-30", level="country", db=trip, travel_only=True)
    assert [s["label"] for s in away] == ["TH"]


def test_get_trips_travel_only(trip):
    from track_me import mcp_server as ms

    out = ms.get_trips(trip, start_date="2019-06-01", end_date="2019-06-09", travel_only=True)
    assert [s["label"] for s in out["stays"]] == ["TH"]
    assert all(s["is_travel"] for s in out["stays"])
