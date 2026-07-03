"""Unit tests for the Django-free SQLite data layer (track_me/db.py)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from track_me.db import Database, Media, Place, local_date_for


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "t.db")
    database.init_schema()
    yield database
    database.close()


def test_schema_creates_tables(db):
    names = {
        r["name"] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"media", "place"} <= names


def test_media_roundtrip(db):
    m = Media(
        dedupe_key="k1",
        file_name="a.jpg",
        taken_at=datetime(2019, 6, 2, 9, 14, tzinfo=UTC),
        taken_at_source="sidecar",
        timezone="Europe/Paris",
        sidecar_raw={"title": "a.jpg", "geo": {"lat": 48.8}},
        needs_review=True,
    )
    m.set_location(48.857, 2.352, "exif_gps")
    db.upsert_media(m)

    got = db.get_media_by_dedupe_key("k1")
    assert got is not None
    assert got.file_name == "a.jpg"
    assert got.taken_at == datetime(2019, 6, 2, 9, 14, tzinfo=UTC)
    assert got.sidecar_raw == {"title": "a.jpg", "geo": {"lat": 48.8}}
    assert got.needs_review is True
    assert got.has_location
    assert got.h3_cell is not None
    # local_date derived from taken_at + Paris tz (UTC+2 in June -> same calendar day)
    assert got.local_date == "2019-06-02"


def test_upsert_is_idempotent_and_preserves_created_at(db):
    m = Media(dedupe_key="k1", file_name="a.jpg")
    first = db.upsert_media(m)
    created = first.created_at

    m.file_name = "renamed.jpg"
    second = db.upsert_media(m)

    assert db.count_media() == 1
    assert second.file_name == "renamed.jpg"
    assert second.created_at == created  # preserved
    assert second.updated_at >= created


def test_local_date_crosses_midnight_abroad():
    # 23:30 UTC in Tokyo (UTC+9) is already the next local day.
    taken = datetime(2019, 6, 2, 23, 30, tzinfo=UTC)
    assert local_date_for(taken, "Asia/Tokyo") == "2019-06-03"
    assert local_date_for(taken, None) == "2019-06-02"


def test_iter_located_ordering_and_year_filter(db):
    db.upsert_media(_located("k2019", datetime(2019, 5, 1, tzinfo=UTC), 40.0, -3.0))
    db.upsert_media(_located("k2020", datetime(2020, 5, 1, tzinfo=UTC), 41.0, -3.0))
    db.upsert_media(_located("k2018", datetime(2018, 5, 1, tzinfo=UTC), 39.0, -3.0))
    # unlocated item is excluded
    db.upsert_media(Media(dedupe_key="noloc", file_name="x.jpg"))

    all_located = db.iter_located()
    assert [m.dedupe_key for m in all_located] == ["k2018", "k2019", "k2020"]
    assert [m.dedupe_key for m in db.iter_located(year=2019)] == ["k2019"]


def test_pending_geocode_filters_on_geo_cell(db):
    db.upsert_media(_located("a", datetime(2019, 1, 1, tzinfo=UTC), 48.8, 2.3))
    db.upsert_media(_located("b", datetime(2019, 1, 2, tzinfo=UTC), 48.9, 2.4))
    assert {m.dedupe_key for m in db.media_pending_geocode()} == {"a", "b"}

    db.upsert_place(Place(h3_cell="cell-x", country_code="FR"))
    db.set_geo_cell("a", "cell-x")
    assert {m.dedupe_key for m in db.media_pending_geocode()} == {"b"}
    assert {m.dedupe_key for m in db.media_pending_geocode(recalculate=True)} == {"a", "b"}


def test_place_roundtrip_and_join(db):
    db.upsert_place(
        Place(
            h3_cell="cell-x",
            city="Paris",
            admin1="Île-de-France",
            country_code="FR",
            formatted_address="Paris, France",
            geocode_raw=[{"types": ["locality"], "long_name": "Paris"}],
            geocoded_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    got = db.get_place("cell-x")
    assert got is not None
    assert got.city == "Paris"
    assert got.geocode_raw == [{"types": ["locality"], "long_name": "Paris"}]

    m = _located("a", datetime(2019, 1, 1, tzinfo=UTC), 48.8, 2.3)
    m.geo_cell = "cell-x"
    db.upsert_media(m)

    rows = db.located_with_place()
    assert len(rows) == 1
    assert rows[0]["city"] == "Paris"
    assert rows[0]["country_code"] == "FR"


def test_derive_workflow(db):
    db.upsert_place(Place(h3_cell="c", geocode_raw=[{"x": 1}]))
    pending = db.places_pending_derive()
    assert [p.h3_cell for p in pending] == ["c"]

    db.update_place_derived("c", city="Lyon", admin1="ARA")
    assert db.get_place("c").city == "Lyon"
    assert db.places_pending_derive() == []


def test_foreign_key_enforced(db):
    m = _located("a", datetime(2019, 1, 1, tzinfo=UTC), 48.8, 2.3)
    db.upsert_media(m)
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        db.set_geo_cell("a", "nonexistent-cell")


def _located(key: str, when: datetime, lat: float, lon: float) -> Media:
    m = Media(dedupe_key=key, file_name=f"{key}.jpg", taken_at=when)
    m.set_location(lat, lon, "exif_gps")
    return m
