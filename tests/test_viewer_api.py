"""API tests for the Flask timeline builder (track_me/viewer/app.py).

Exercises the builder endpoints against a throwaway SQLite DB and temp timelines
dir — same throwaway-DB pattern as tests/test_db.py, driven through Flask's test
client. Asserts server/CLI parity, the save round-trip, and id sanitization.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from track_me import config
from track_me import timeline as tl
from track_me.db import Database, Media, Place
from track_me.viewer import app as viewer_app


def _seed(db: Database, dedupe: str, lat: float, lng: float, city: str, cc: str, taken_at):
    """One located+geocoded photo joined to its place (via geo_cell → h3_cell)."""
    m = Media(
        dedupe_key=dedupe,
        google_photos_url=f"https://photos.google.com/photo/{dedupe}",
        taken_at=taken_at,
        timezone="UTC",
    )
    m.set_location(lat, lng, "exif_gps")
    m.refresh_local_date()
    cell = m.geo_cell_at(9)
    assert cell is not None  # set_location just populated h3_cell
    m.geo_cell = cell
    db.upsert_place(
        Place(h3_cell=cell, center_lat=lat, center_lng=lng, city=city, country_code=cc)
    )
    db.upsert_media(m)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Flask test client wired to a temp DB + temp timelines dir, seeded with a
    France → Japan trip (three Paris photos, then two Tokyo photos)."""
    db_path = tmp_path / "t.db"
    timelines_dir = tmp_path / "timelines"
    timelines_dir.mkdir()

    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "USERDATA_DIR", tmp_path)
    monkeypatch.setattr(config, "THUMBNAIL_CACHE_DIR", tmp_path / "thumbnails")
    monkeypatch.setattr(config, "TIMELINES_DIR", timelines_dir)
    monkeypatch.setattr(config, "GOOGLE_MAPS_API_KEY", "test-key")
    monkeypatch.setattr(viewer_app, "TIMELINES_DIR", timelines_dir)

    db = Database(db_path)
    db.init_schema()
    _seed(db, "p1", 48.857, 2.352, "Paris", "FR", datetime(2019, 6, 1, 9, tzinfo=UTC))
    _seed(db, "p2", 48.861, 2.336, "Paris", "FR", datetime(2019, 6, 2, 10, tzinfo=UTC))
    _seed(db, "p3", 48.853, 2.349, "Paris", "FR", datetime(2019, 6, 3, 11, tzinfo=UTC))
    _seed(db, "j1", 35.681, 139.767, "Tokyo", "JP", datetime(2019, 6, 6, 9, tzinfo=UTC))
    _seed(db, "j2", 35.689, 139.700, "Tokyo", "JP", datetime(2019, 6, 7, 10, tzinfo=UTC))
    db.close()

    return viewer_app.app.test_client()


def test_preview_matches_build_stays(client):
    """/api/preview stays are identical to a direct tl.build_stays() call."""
    resp = client.get(
        "/api/preview",
        query_string={"start": "2019-01-01", "end": "2020-01-01", "level": "country"},
    )
    assert resp.status_code == 200
    got = resp.get_json()["stays"]

    expected = tl.build_stays("2019-01-01", "2020-01-01", level="country")
    assert got == expected
    # sanity: the trip resolves to two country stays (France, then Japan)
    assert [s["label"] for s in got] == ["FR", "JP"]


def test_preview_city_level_knobs(client):
    resp = client.get(
        "/api/preview",
        query_string={
            "start": "2019-01-01",
            "end": "2020-01-01",
            "level": "city",
            "merge_km": "50",
            "min_hours": "24",
        },
    )
    assert resp.status_code == 200
    got = resp.get_json()["stays"]
    expected = tl.build_stays(
        "2019-01-01", "2020-01-01", level="city", merge_km=50.0, min_hours=24
    )
    assert got == expected
    assert [s["label"] for s in got] == ["Paris", "Tokyo"]


def test_points_payload_and_region_filter(client):
    resp = client.get("/api/points")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["point_fields"] == tl.POINT_FIELDS
    assert len(data["points"]) == 5  # all located photos

    ccs = {row[data["point_fields"].index("cc")] for row in data["points"]}
    assert ccs == {"FR", "JP"}

    filtered = client.get("/api/points", query_string={"region": "JP"}).get_json()
    assert len(filtered["points"]) == 2


def test_save_roundtrips_through_viewer(client):
    resp = client.post(
        "/api/timeline",
        json={
            "id": "trip-2019",
            "title": "France then Japan",
            "start": "2019-01-01",
            "end": "2020-01-01",
            "level": "country",
        },
    )
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["url"] == "/t/trip-2019"

    # file written with the persisted build block + points payload
    written = config.TIMELINES_DIR / "trip-2019.json"
    assert written.is_file()
    doc = json.loads(written.read_text())
    assert doc["build"]["level"] == "country"
    assert doc["build"]["start"] == "2019-01-01"
    assert "points" in doc

    # round-trips through the existing read routes
    assert client.get("/t/trip-2019").status_code == 200
    data = client.get("/timeline/trip-2019.json").get_json()
    assert data["title"] == "France then Japan"
    assert [s["label"] for s in data["stays"]] == ["FR", "JP"]


def test_save_can_reload_via_build_route(client):
    client.post(
        "/api/timeline",
        json={
            "id": "reloadable",
            "title": "Reloadable",
            "start": "2019-05-01",
            "end": "2019-08-01",
            "level": "city",
            "merge_km": 40,
            "min_hours": 12,
            "region": ["FR"],
        },
    )
    # /build/<id> renders (prefill is injected server-side); just assert it serves
    assert client.get("/build/reloadable").status_code == 200


@pytest.mark.parametrize("bad_id", ["../foo", "a/b", "Foo", "-bad", "", "a b"])
def test_bad_id_rejected(client, bad_id):
    resp = client.post(
        "/api/timeline",
        json={"id": bad_id, "title": "x", "start": "2019-01-01", "end": "2020-01-01"},
    )
    assert resp.status_code == 400
    # nothing written
    assert list(config.TIMELINES_DIR.glob("*.json")) == []


def test_missing_title_rejected(client):
    resp = client.post(
        "/api/timeline",
        json={"id": "ok-id", "title": "", "start": "2019-01-01", "end": "2020-01-01"},
    )
    assert resp.status_code == 400
