"""Tests for the MCP server's read-only catalog tools and its email allowlist."""

import asyncio
import os
import sqlite3
import sys
from datetime import datetime
from datetime import timezone as dt_timezone

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.providers.google import GoogleProvider

from track_me import config
from track_me import mcp_server as ms
from track_me.db import Database, LocationSource, Media, Place


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "t.db")
    database.init_schema()
    yield database
    database.close()


def _photo(db, key, when, lat, lon, cell, tz="UTC"):
    m = Media(
        dedupe_key=key,
        file_name=f"{key}.jpg",
        taken_at=when,
        timezone=tz,
        google_photos_url=f"https://photos.google.com/photo/{key}",
    )
    m.set_location(lat, lon, source=LocationSource.EXIF_GPS)
    m.geo_cell = cell
    m.refresh_local_date()
    db.upsert_media(m)


@pytest.fixture
def trip(db):
    # Singapore -> Tokyo (two wards, one metro) -> Kyoto -> Singapore, June 2019.
    db.upsert_place(Place(h3_cell="cSG", city="Singapore", country_code="SG"))
    db.upsert_place(Place(h3_cell="cCH", city="Chiyoda City", admin1="Tokyo", country_code="JP"))
    db.upsert_place(Place(h3_cell="cTA", city="Taito City", admin1="Tokyo", country_code="JP"))
    db.upsert_place(
        Place(
            h3_cell="cKY",
            city="Kyoto",
            admin1="Kyoto",
            country_code="JP",
            formatted_address="Higashiyama Ward, Kyoto, Japan",
        )
    )
    d = lambda day, h=3: datetime(2019, 6, day, h, 0, tzinfo=dt_timezone.utc)  # noqa: E731
    _photo(db, "s1", d(1), 1.30, 103.80, "cSG", "Asia/Singapore")
    _photo(db, "t1", d(3), 35.68, 139.76, "cCH", "Asia/Tokyo")
    _photo(db, "t2", d(4), 35.71, 139.79, "cTA", "Asia/Tokyo")
    _photo(db, "t3", d(4, 5), 35.71, 139.79, "cTA", "Asia/Tokyo")
    _photo(db, "k1", d(6), 35.00, 135.77, "cKY", "Asia/Tokyo")
    _photo(db, "k2", d(7), 35.00, 135.78, "cKY", "Asia/Tokyo")
    _photo(db, "s2", d(9), 1.30, 103.80, "cSG", "Asia/Singapore")
    return db


# --- tool logic ------------------------------------------------------------
def test_catalog_overview(trip):
    out = ms.catalog_overview(trip)
    assert out["total"] == 7 and out["located"] == 7
    assert (out["first_date"], out["last_date"]) == ("2019-06-01", "2019-06-09")
    assert {c["country_code"]: c["photo_count"] for c in out["countries"]} == {"JP": 5, "SG": 2}
    assert out["today"]


def test_find_places_matches_city_and_address(trip):
    names = {p["city"] for p in ms.find_places(trip, "kyo")["places"]}
    assert names == {"Kyoto", "Chiyoda City", "Taito City"}  # Kyoto + Tokyo (admin1)
    assert ms.find_places(trip, "Higashiyama")["places"][0]["photo_count"] == 2


def test_find_photos_filters_and_formats(trip):
    out = ms.find_photos(trip, country="JP", start_date="2019-06-04", end_date="2019-06-06")
    assert out["total_matches"] == 3 and not out["sampled"]
    first = out["photos"][0]
    assert first["city"] == "Tokyo"  # metro rollup via timeline.city_label
    assert first["local_time"] == "2019-06-04 12:00 JST"
    assert first["google_photos_url"] == "https://photos.google.com/photo/t2"
    assert first["maps_url"].endswith("query=35.71000,139.79000")


def test_find_photos_city_matches_admin1_metro(trip):
    assert ms.find_photos(trip, city="tokyo")["total_matches"] == 3


def test_find_photos_near_and_sampling(trip):
    near = ms.find_photos(trip, near_lat=1.30, near_lng=103.80, radius_km=1)
    assert near["total_matches"] == 2
    sampled = ms.find_photos(trip, limit=3)
    assert sampled["total_matches"] == 7 and sampled["sampled"]
    ids = [p["file_name"] for p in sampled["photos"]]
    assert ids[0] == "s1.jpg" and ids[-1] == "s2.jpg"  # spread keeps both ends


def test_get_trips_country_and_city(trip):
    countries = ms.get_trips(trip, start_date="2019-06-01", end_date="2019-06-09")
    assert [s["label"] for s in countries["stays"]] == ["SG", "JP", "SG"]
    assert countries["stays"][1]["maps_url"].startswith("https://www.google.com/maps/")
    cities = ms.get_trips(
        trip, start_date="2019-06-02", end_date="2019-06-08", level="city", country="JP"
    )
    assert [s["label"] for s in cities["stays"]] == ["Tokyo", "Kyoto"]


def test_get_trips_end_date_is_inclusive(trip):
    out = ms.get_trips(trip, start_date="2019-06-09", end_date="2019-06-09")
    assert out["stay_count"] == 1 and out["stays"][0]["label"] == "SG"


def test_place_stats(trip):
    by_country = ms.place_stats(trip)["groups"]
    assert by_country[0] == {
        "country": "JP",
        "photo_count": 5,
        "days_with_photos": 4,
        "first_date": "2019-06-03",
        "last_date": "2019-06-07",
    }
    by_city = ms.place_stats(trip, group_by="city", country="JP")["groups"]
    assert [(g["city"], g["photo_count"]) for g in by_city] == [("Tokyo", 3), ("Kyoto", 2)]


def test_busiest_days(trip):
    days = ms.busiest_days(trip, start_date="2019-06-01", end_date="2019-06-30", limit=1)
    assert days["days"] == [
        {"date": "2019-06-04", "photo_count": 2, "cities": ["Tokyo"], "countries": ["JP"]}
    ]


# --- server wiring ---------------------------------------------------------
def test_server_lists_and_calls_tools(trip, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", trip.path)

    async def go():
        async with Client(ms.create_server()) as client:
            tools = {t.name: t for t in await client.list_tools()}
            result = await client.call_tool("place_stats", {"group_by": "city"})
            return tools, result

    tools, result = asyncio.run(go())
    assert set(tools) == {
        "catalog_overview",
        "find_places",
        "find_photos",
        "get_trips",
        "place_stats",
        "busiest_days",
    }
    assert all(t.annotations and t.annotations.readOnlyHint for t in tools.values())
    assert result.structured_content["groups"][0]["city"] == "Tokyo"


def test_run_refuses_no_auth_off_loopback(trip, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", trip.path)
    with pytest.raises(SystemExit, match="loopback"):
        ms.run(host="0.0.0.0", port=8080, no_auth=True)


def test_run_requires_existing_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "missing.db")
    with pytest.raises(SystemExit, match="catalog not found"):
        ms.run(host="127.0.0.1", port=8080, stdio=True)


def test_open_db_is_read_only_and_lock_free(trip, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", trip.path)
    with ms._open_db() as db:
        assert db.count_media() == 7
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.conn.execute("DELETE FROM media")
        # Immutable: no lock is taken, so a writer can commit even while one of
        # our queries is mid-read (a plain mode=ro reader would hold SHARED and
        # the commit would fail with "database is locked").
        cur = db.conn.execute("SELECT id FROM media")
        cur.fetchone()
        trip.conn.execute("UPDATE media SET needs_review = 1")
        trip.conn.commit()
        cur.close()
    assert not trip.path.with_name(trip.path.name + "-journal").exists()


def test_stdio_session_reads_cwd_userdata(trip, tmp_path):
    """End to end: a client launches `track-me mcp --stdio` from a directory
    holding userdata/track_me.db (the default location) and calls a tool."""
    workdir = tmp_path / "work"
    (workdir / "userdata").mkdir(parents=True)
    trip.conn.execute("VACUUM INTO ?", (str(workdir / "userdata" / "track_me.db"),))
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"DB_PATH", "TRACKME_USERDATA", "USERDATA_DIR"}
    }
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "track_me.cli", "mcp", "--stdio"],
        env=env,
        cwd=str(workdir),
    )

    async def go():
        async with Client(transport) as client:
            return await client.call_tool("catalog_overview", {})

    result = asyncio.run(go())
    assert result.structured_content["total"] == 7


def test_build_auth_requires_config(monkeypatch):
    monkeypatch.setattr(config, "MCP_BASE_URL", "")
    with pytest.raises(SystemExit, match="MCP_BASE_URL"):
        ms.build_auth()


# --- allowlist -------------------------------------------------------------
def _provider(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMCP_HOME", str(tmp_path))
    return ms.AllowlistGoogleProvider(
        client_id="cid.apps.googleusercontent.com",
        client_secret="test-secret-value",
        base_url="https://example.run.app",
        allowed_emails=frozenset({"Me@Example.com"}),
    )


def _token(email, verified=True):
    return AccessToken(
        token="t",
        client_id="c",
        scopes=["openid"],
        claims={"email": email, "email_verified": verified},
    )


@pytest.mark.parametrize(
    ("token", "allowed"),
    [
        (_token("me@example.com"), True),
        (_token("ME@example.com"), True),
        (_token("me@example.com", verified=False), False),
        (_token("someone@example.com"), False),
        (None, False),
    ],
)
def test_allowlist_is_allowed(tmp_path, monkeypatch, token, allowed):
    assert _provider(tmp_path, monkeypatch).is_allowed(token) is allowed


def test_allowlist_rejects_other_accounts_at_token_load(tmp_path, monkeypatch):
    provider = _provider(tmp_path, monkeypatch)
    upstream = {"value": _token("someone@example.com")}

    async def fake_load(self, token):
        return upstream["value"]

    monkeypatch.setattr(GoogleProvider, "load_access_token", fake_load)
    assert asyncio.run(provider.load_access_token("x")) is None
    upstream["value"] = _token("me@example.com")
    assert asyncio.run(provider.load_access_token("x")) is upstream["value"]


def test_allowlist_must_not_be_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTMCP_HOME", str(tmp_path))
    with pytest.raises(ValueError):
        ms.AllowlistGoogleProvider(
            client_id="cid",
            client_secret="s",
            base_url="https://example.run.app",
            allowed_emails=frozenset(),
        )


# --- cold-start DB copy ----------------------------------------------------
def test_fetch_db_noop_without_uri(monkeypatch, tmp_path):
    db_path = tmp_path / "t.db"
    monkeypatch.setattr(config, "DB_GCS_URI", "")
    monkeypatch.setattr(config, "DB_PATH", db_path)
    ms.fetch_db()
    assert config.DB_PATH == db_path


def test_fetch_db_copies_and_repoints(monkeypatch, tmp_path):
    local = tmp_path / "tmp" / "track_me.db"
    calls = []

    def fake_download(uri, dest):
        calls.append(uri)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"sqlite")
        return dest

    monkeypatch.setattr(config, "DB_GCS_URI", "gs://bkt/track_me/track_me.db")
    monkeypatch.setattr(config, "DB_LOCAL_COPY", local)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "unused.db")
    monkeypatch.setattr(ms.storage, "download_gcs", fake_download)
    ms.fetch_db()
    assert calls == ["gs://bkt/track_me/track_me.db"]
    assert config.DB_PATH == local


def test_download_gcs_rejects_bad_uri(tmp_path):
    from track_me import storage

    with pytest.raises(ValueError, match="gs://bucket/key"):
        storage.download_gcs("gs://bucket-only", tmp_path / "x.db")
