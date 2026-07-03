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


def test_document_and_write(trip, tmp_path, monkeypatch):
    monkeypatch.setattr(tl.config, "TIMELINES_DIR", tmp_path / "timelines")
    stays = tl.build_stays("2019-06-01", "2019-07-01", level="country", db=trip)
    doc = tl.to_document(stays, timeline_id="t2019", title="Test 2019", prompts=["build it"])
    assert doc["prompts"] == ["build it"]
    assert doc["generated_at"].endswith("+00:00")
    out = tl.write_timeline(doc)
    assert out.exists()
    assert out.name == "t2019.json"
