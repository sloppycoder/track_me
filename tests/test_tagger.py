from datetime import UTC, datetime

import pytest
from starlette.testclient import TestClient

from track_me.db import Database, LocationSource, Media
from track_me.tagger import ResolveError, build_clusters, create_app, resolve_place

K = [f"{i:040x}" for i in range(1, 8)]  # 40-hex keys, like real dedupe_keys


def _photo(db, key, day, *, lat=None, kind="photo", hour=12):
    m = Media(dedupe_key=key, file_name=f"{key[-4:]}.jpg", kind=kind)
    y, mo, d = map(int, day.split("-"))
    m.taken_at = datetime(y, mo, d, hour, tzinfo=UTC)
    m.google_photos_url = f"https://photos.google.com/photo/{key[-4:]}"
    if lat is not None:
        m.set_location(lat, 10.0, LocationSource.TAKEOUT)
    m.needs_review = lat is None
    db.upsert_media(m)


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "t.db")
    database.init_schema()
    yield database
    database.close()


@pytest.fixture
def thumbs(tmp_path):
    d = tmp_path / "thumbs"
    d.mkdir()
    return d


@pytest.fixture
def client(db, thumbs):
    return TestClient(
        create_app(db.path, thumbs, max_photos=2, gap_days=3, geocode_client=_Fake())
    )


class _Fake:
    def geocode(self, query, language="en"):
        if "nowhere" in query:
            return []
        return [
            {
                "formatted_address": "MP5W+C5 Minato City, Tokyo, Japan",
                "geometry": {"location": {"lat": 35.6586, "lng": 139.7454}},
            }
        ]


# --- clustering -------------------------------------------------------------
def test_build_clusters_splits_on_gap():
    days = [("2006-12-18", 3), ("2006-12-20", 2), ("2006-12-23", 5), ("2007-05-01", 1)]
    assert build_clusters(days, gap_days=3) == [
        {"start": "2006-12-18", "end": "2006-12-23", "count": 10},  # 3-day gaps stay together
        {"start": "2007-05-01", "end": "2007-05-01", "count": 1},
    ]
    assert len(build_clusters(days, gap_days=1)) == 4
    assert len(build_clusters([("2006-12-18", 1), ("2006-12-22", 1)], gap_days=3)) == 2  # 4 days
    assert build_clusters([], gap_days=3) == []


# --- resolving a lane's place ----------------------------------------------
def test_resolve_lat_lng_is_offline():
    assert resolve_place("35.6586, 139.7454") == {
        "lat": 35.6586,
        "lng": 139.7454,
        "label": "35.65860, 139.74540",
    }
    with pytest.raises(ResolveError):
        resolve_place("95, 10")


def test_resolve_plus_code_uses_geocoder():
    r = resolve_place("MP5W+C5 Minato City, Tokyo, Japan", _Fake())
    assert (r["lat"], r["lng"]) == (35.6586, 139.7454)
    assert "Minato" in r["label"]
    with pytest.raises(ResolveError, match="No match"):
        resolve_place("nowhere", _Fake())
    with pytest.raises(ResolveError):
        resolve_place("   ", _Fake())


def test_resolve_without_api_key(monkeypatch):
    monkeypatch.setattr("track_me.config.GOOGLE_MAPS_API_KEY", None)
    with pytest.raises(ResolveError, match="GOOGLE_MAPS_API_KEY"):
        resolve_place("Taipei")


# --- Database.tag_manual ----------------------------------------------------
def test_tag_manual_sets_location_and_rebuckets_day(db):
    _photo(db, K[0], "2006-12-31", hour=20)  # 20:00 UTC = 05:00 next day in Tokyo
    assert db.tag_manual([(K[0], 35.6586, 139.7454)]) == 1
    m = db.get_media_by_dedupe_key(K[0])
    assert m.location_source == LocationSource.MANUAL
    assert (m.latitude, m.longitude) == (35.6586, 139.7454)
    assert m.h3_cell and m.geo_cell is None  # geocode links it later
    assert m.timezone == "Asia/Tokyo"
    assert m.local_date == "2007-01-01"
    assert not m.needs_review


def test_tag_manual_never_overwrites_a_located_photo(db):
    _photo(db, K[0], "2019-05-01", lat=1.0)
    assert db.tag_manual([(K[0], 35.0, 139.0), (K[1], 35.0, 139.0)]) == 0  # located / unknown
    assert db.get_media_by_dedupe_key(K[0]).latitude == 1.0


def test_untagged_queries_skip_videos_and_located(db):
    _photo(db, K[0], "2006-12-18")
    _photo(db, K[1], "2006-12-19")
    _photo(db, K[2], "2006-12-19", lat=5.0)
    _photo(db, K[3], "2006-12-19", kind="video")
    assert db.untagged_day_counts() == [("2006-12-18", 1), ("2006-12-19", 1)]
    assert db.count_untagged("2006-12-19", "2006-12-19") == 1
    assert [p["dedupe_key"] for p in db.untagged_photos("2006-12-18", "2006-12-19", 10)] == K[:2]
    assert len(db.untagged_photos("2006-12-18", "2006-12-19", 1)) == 1


# --- HTTP -------------------------------------------------------------------
def test_page_and_clusters(client, db):
    assert "Tag photos" in client.get("/").text
    for key, day in zip(K[:3], ["2006-12-18", "2006-12-20", "2007-05-01"], strict=True):
        _photo(db, key, day)
    body = client.get("/api/clusters").json()
    assert body["total"] == 3 and body["max_photos"] == 2
    assert [(c["start"], c["end"], c["count"]) for c in body["clusters"]] == [
        ("2006-12-18", "2006-12-20", 2),
        ("2007-05-01", "2007-05-01", 1),
    ]
    assert body["clusters"][0]["samples"] == K[:2]


def test_photos_are_capped_at_max_and_carry_the_photos_link(client, db):
    for key in K[:3]:
        _photo(db, key, "2006-12-18")
    body = client.get("/api/photos", params={"start": "2006-12-18", "end": "2006-12-18"}).json()
    assert (body["total"], body["shown"]) == (3, 2)
    assert body["photos"][0]["url"].startswith("https://photos.google.com/")
    assert client.get("/api/photos", params={"start": "x", "end": "y"}).status_code == 400


def test_resolve_endpoint(client):
    ok = client.post("/api/resolve", json={"query": "MP5W+C5 Minato City, Tokyo, Japan"})
    assert ok.status_code == 200 and ok.json()["lat"] == 35.6586
    assert client.post("/api/resolve", json={"query": "nowhere"}).status_code == 422
    assert client.post("/api/resolve", json={"nope": 1}).status_code == 400


def test_save_tags_writes_and_logs_once(client, db):
    for key in K[:3]:
        _photo(db, key, "2006-12-18")
    lanes = [
        {"label": "Tokyo", "lat": 35.6586, "lng": 139.7454, "keys": K[:2]},
        {"label": "Empty lane", "lat": 1.0, "lng": 2.0, "keys": []},
    ]
    assert client.post("/api/tags", json={"lanes": lanes}).json() == {"tagged": 2, "skipped": 0}
    assert db.get_media_by_dedupe_key(K[0]).location_source == LocationSource.MANUAL
    assert db.get_media_by_dedupe_key(K[2]).latitude is None
    log = db.recent_changes(1)[0]
    assert log["command"] == "tag" and log["details"]["photos"] == 2
    assert log["details"]["places"] == ["Tokyo"]

    # saving the same photos again changes nothing and logs nothing
    n = len(db.recent_changes(100))
    again = client.post("/api/tags", json={"lanes": lanes}).json()
    assert again == {"tagged": 0, "skipped": 2}
    assert len(db.recent_changes(100)) == n


def test_save_tags_validation(client):
    bad_key = {"lanes": [{"label": "x", "lat": 1, "lng": 2, "keys": ["../../etc/passwd"]}]}
    assert client.post("/api/tags", json=bad_key).status_code == 400
    bad_lat = {"lanes": [{"label": "x", "lat": 91, "lng": 2, "keys": K[:1]}]}
    assert client.post("/api/tags", json=bad_lat).status_code == 422
    assert client.post("/api/tags", json={}).status_code == 400
    assert client.post("/api/tags", json={"lanes": []}).json() == {"tagged": 0, "skipped": 0}


def test_thumbnail_route_serves_only_valid_keys(client, thumbs):
    (thumbs / f"{K[0]}.jpg").write_bytes(b"\xff\xd8jpeg")
    (thumbs.parent / "secret.jpg").write_bytes(b"secret")
    ok = client.get(f"/thumb/{K[0]}")
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/jpeg"
    assert client.get(f"/thumb/{K[1]}").status_code == 404  # no such thumbnail
    assert client.get("/thumb/..%2Fsecret").status_code == 404  # traversal
    assert client.get("/thumb/secret").status_code == 404  # not a 40-hex key
