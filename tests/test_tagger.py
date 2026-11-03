from datetime import UTC, datetime

import pytest
from starlette.testclient import TestClient

from track_me.db import Database, LocationSource, ManualEdit, Media, TimeSource
from track_me.tagger import (
    ResolveError,
    build_clusters,
    camera_label,
    create_app,
    resolve_place,
)

K = [f"{i:040x}" for i in range(1, 8)]  # 40-hex keys, like real dedupe_keys


def _photo(db, key, day, *, lat=None, kind="photo", hour=12, source=TimeSource.SIDECAR):
    m = Media(dedupe_key=key, file_name=f"{key[-4:]}.jpg", kind=kind)
    y, mo, d = map(int, day.split("-"))
    m.taken_at = datetime(y, mo, d, hour, tzinfo=UTC)
    m.taken_at_source = source
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
        create_app(
            db.path,
            thumbs,
            max_photos=2,
            gap_days=3,
            geocode_client=_Fake(),
            generic_dates={"2000-12-31", "2001-01-01"},
        )
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


# --- camera names -------------------------------------------------------------
def test_camera_label_from_exif_then_prefix():
    assert camera_label("NIKON", "E885", "DSCN0678.JPG") == "Nikon E885"
    assert camera_label("NIKON CORPORATION", "NIKON D300", "x.jpg") == "Nikon D300"
    assert camera_label("Canon", "Canon EOS DIGITAL REBEL", "x.jpg") == "Canon EOS DIGITAL REBEL"
    assert camera_label("OLYMPUS IMAGING CORP.  ", "XZ-1            ", "x.jpg").endswith("XZ-1")
    assert camera_label("SONY", "DSC-W5", "DSC00012.JPG") == "Sony DSC-W5"
    assert camera_label("Apple", "iPhone 4S", "IMG_1.JPG") == "Apple iPhone 4S"
    assert camera_label(None, None, "DSCN0001.JPG") == "Nikon Coolpix"  # no EXIF: known prefix
    assert camera_label(None, None, "IMG_0001.JPG") == "IMG_"  # ambiguous: show the prefix
    assert camera_label("", "", "20190101.jpg") == "Unknown camera"


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
    assert db.tag_manual([ManualEdit(K[0], 35.6586, 139.7454)]) == [K[0]]
    m = db.get_media_by_dedupe_key(K[0])
    assert m.location_source == LocationSource.MANUAL
    assert (m.latitude, m.longitude) == (35.6586, 139.7454)
    assert m.h3_cell and m.geo_cell is None  # geocode links it later
    assert m.timezone == "Asia/Tokyo"
    assert m.local_date == "2007-01-01"
    assert not m.needs_review


def test_tag_manual_never_overwrites_a_located_photo(db):
    _photo(db, K[0], "2019-05-01", lat=1.0)
    assert db.tag_manual([ManualEdit(K[0], 35.0, 139.0), ManualEdit(K[1], 35.0, 139.0)]) == []
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


def test_tag_manual_date_only_sets_noon_local_and_manual_source(db):
    _photo(db, K[0], "2000-12-31", hour=21)
    assert db.tag_manual([ManualEdit(K[0], date="2007-07-09")]) == [K[0]]
    m = db.get_media_by_dedupe_key(K[0])
    assert m.taken_at == datetime(2007, 7, 9, 12, tzinfo=UTC)  # no timezone known -> UTC noon
    assert m.taken_at_source == TimeSource.MANUAL
    assert m.local_date == "2007-07-09"
    assert m.latitude is None  # still untagged: only the date was fixed


def test_tag_manual_date_with_location_is_noon_in_that_timezone(db):
    _photo(db, K[0], "2000-12-31", hour=21)
    db.tag_manual([ManualEdit(K[0], 35.6586, 139.7454, "2007-07-09")])
    m = db.get_media_by_dedupe_key(K[0])
    assert m.taken_at == datetime(2007, 7, 9, 3, tzinfo=UTC)  # 12:00 in Tokyo (UTC+9)
    assert m.local_date == "2007-07-09" and m.timezone == "Asia/Tokyo"


def test_adding_location_later_keeps_the_manual_day(db):
    _photo(db, K[0], "2000-12-31", hour=21)
    db.tag_manual([ManualEdit(K[0], date="2007-07-09")])  # UTC noon
    db.tag_manual([ManualEdit(K[0], -36.85, 174.76)])  # Auckland, UTC+12/13
    m = db.get_media_by_dedupe_key(K[0])
    assert m.timezone == "Pacific/Auckland"
    assert m.local_date == "2007-07-09"  # not shifted to the 10th
    assert m.taken_at_source == TimeSource.MANUAL


def test_trusted_dates_excludes_placeholders_and_manual(db):
    generic = {"2000-12-31", "2001-01-01"}
    _photo(db, K[0], "2000-12-31")
    _photo(db, K[1], "2007-07-09")
    _photo(db, K[2], "2008-08-13", source=TimeSource.MANUAL)
    _photo(db, K[3], "2009-01-01", lat=1.0)  # located: not editable at all
    assert db.trusted_dates(K[:4], generic) == {K[1]: "2007-07-09"}


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


def test_clusters_and_photos_flag_placeholder_dates(client, db):
    _photo(db, K[0], "2000-12-31")
    _photo(db, K[1], "2007-07-09")
    body = client.get("/api/clusters").json()
    assert {c["start"]: c["generic"] for c in body["clusters"]} == {
        "2000-12-31": 1,
        "2007-07-09": 0,
    }
    ph = client.get("/api/photos", params={"start": "2000-12-31", "end": "2007-07-09"}).json()
    flags = {p["key"]: (p["generic"], p["trusted"]) for p in ph["photos"]}
    assert flags == {K[0]: (True, False), K[1]: (False, True)}
    assert all(p["camera"] == "Unknown camera" for p in ph["photos"])  # fixtures: no EXIF


def test_save_date_only_lane_and_log(client, db):
    _photo(db, K[0], "2000-12-31")
    _photo(db, K[1], "2000-12-31")
    lanes = [{"label": "", "date": "2007-07-09", "keys": K[:2]}]
    assert client.post("/api/tags", json={"lanes": lanes}).json() == {"tagged": 2, "skipped": 0}
    assert db.get_media_by_dedupe_key(K[0]).local_date == "2007-07-09"
    log = db.recent_changes(1)[0]
    assert log["details"]["redated"] == 2 and log["details"]["located"] == 0


def test_redating_a_trusted_date_needs_confirmation(client, db):
    _photo(db, K[0], "2007-07-09")
    lanes = [{"label": "", "date": "2008-08-13", "keys": K[:1]}]
    logged = len(db.recent_changes(100))
    r = client.post("/api/tags", json={"lanes": lanes})
    assert r.status_code == 409
    assert r.json()["needs_confirmation"] and r.json()["dates"] == ["2007-07-09"]
    assert db.get_media_by_dedupe_key(K[0]).local_date == "2007-07-09"  # nothing written
    assert len(db.recent_changes(100)) == logged

    ok = client.post("/api/tags", json={"lanes": lanes, "confirm_overwrite": True})
    assert ok.json() == {"tagged": 1, "skipped": 0}
    assert db.get_media_by_dedupe_key(K[0]).local_date == "2008-08-13"
    assert db.recent_changes(1)[0]["details"]["overwrote_trusted_dates"] == 1
    # now it is a manual date, so changing it again needs no confirmation
    again = [{"label": "", "date": "2008-08-14", "keys": K[:1]}]
    assert client.post("/api/tags", json={"lanes": again}).json()["tagged"] == 1


def test_location_only_lane_never_asks_for_date_confirmation(client, db):
    _photo(db, K[0], "2007-07-09")
    lanes = [{"label": "Tokyo", "lat": 35.6586, "lng": 139.7454, "keys": K[:1]}]
    assert client.post("/api/tags", json={"lanes": lanes}).json()["tagged"] == 1


def test_save_tags_validation(client):
    assert client.post("/api/tags", json={"lanes": [{"keys": K[:1]}]}).status_code == 400
    for bad in ("2007-13-01", "9999-01-01", "x"):
        lane = {"date": bad, "keys": K[:1]}
        assert client.post("/api/tags", json={"lanes": [lane]}).status_code == 422
    half = {"lat": 1, "keys": K[:1], "date": "2007-01-01"}
    assert client.post("/api/tags", json={"lanes": [half]}).status_code == 400
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
