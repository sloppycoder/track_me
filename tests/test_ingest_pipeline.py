"""End-to-end ingestion tests against a synthetic Takeout tree."""

import json
from datetime import datetime
from datetime import timezone as dt_timezone
from pathlib import Path

import pytest
from PIL import Image

from library.ingest.pipeline import IngestPipeline, _resolve_taken_at, compute_dedupe_key
from track_me.db import Database, LocationSource, Media, TimeSource

TOKYO = (35.6895, 139.6917)


def _jpeg(path, color=(120, 120, 120)):
    Image.new("RGB", (64, 48), color).save(path, "JPEG")


def _sidecar(path, **payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "track_me.db")
    database.init_schema()
    yield database
    database.close()


@pytest.fixture
def thumbs_dir(tmp_path):
    return tmp_path / "thumbs"


def _pipeline(db, thumbs_dir, *, generate_thumbnails=False):
    return IngestPipeline(
        db,
        generate_thumbnails=generate_thumbnails,
        thumbnail_cache_dir=thumbs_dir,
        thumbnail_size=(100, 100),
    )


def _by_name(db: Database, name: str) -> Media:
    row = db.conn.execute("SELECT dedupe_key FROM media WHERE file_name = ?", (name,)).fetchone()
    assert row is not None, f"no media row named {name}"
    item = db.get_media_by_dedupe_key(row["dedupe_key"])
    assert item is not None
    return item


def _all(db: Database) -> list[Media]:
    keys = [r["dedupe_key"] for r in db.conn.execute("SELECT dedupe_key FROM media")]
    items = [db.get_media_by_dedupe_key(k) for k in keys]
    return [i for i in items if i is not None]


@pytest.fixture
def takeout(tmp_path):
    """A small Takeout-like tree."""
    root = tmp_path / "Takeout" / "Google Photos" / "Photos from 2019"
    root.mkdir(parents=True)

    # 1. located photo with full sidecar
    _jpeg(root / "IMG_1.JPG", (10, 20, 30))
    _sidecar(
        root / "IMG_1.JPG.json",
        title="IMG_1.JPG",
        url="https://photos.google.com/photo/AAA",
        photoTakenTime={"timestamp": "1564920000"},
        geoData={"latitude": TOKYO[0], "longitude": TOKYO[1]},
    )

    # 2. sidecar present but NO location (geoData 0,0) -- still gets time + url
    _jpeg(root / "IMG_2.JPG", (40, 50, 60))
    _sidecar(
        root / "IMG_2.JPG.supplemental-metadata.json",
        title="IMG_2.JPG",
        url="https://photos.google.com/photo/BBB",
        photoTakenTime={"timestamp": "1564930000"},
        geoData={"latitude": 0.0, "longitude": 0.0},
    )

    # 3. orphan photo, no sidecar at all -- time falls back to file mtime
    _jpeg(root / "IMG_3.JPG", (70, 80, 90))

    return str(tmp_path / "Takeout")


def test_ingest_creates_items(db, thumbs_dir, takeout):
    stats = _pipeline(db, thumbs_dir).ingest_directory(takeout)
    assert stats.total_files == 3
    assert stats.created == 3
    assert db.count_media() == 3


def test_located_photo(db, thumbs_dir, takeout):
    _pipeline(db, thumbs_dir).ingest_directory(takeout)
    item = _by_name(db, "IMG_1.JPG")
    assert item.has_location
    assert item.location_source == LocationSource.TAKEOUT
    assert item.taken_at_source == TimeSource.SIDECAR
    assert item.taken_at is not None
    assert item.google_photos_url == "https://photos.google.com/photo/AAA"
    assert item.h3_cell  # computed from coords
    assert item.timezone == "Asia/Tokyo"  # offline-derived from coords
    assert item.local_date is not None  # local-day bucket set at ingest
    assert not item.needs_review


def test_sidecar_without_location_still_has_time_and_link(db, thumbs_dir, takeout):
    _pipeline(db, thumbs_dir).ingest_directory(takeout)
    item = _by_name(db, "IMG_2.JPG")
    assert not item.has_location
    assert item.location_source == LocationSource.NONE
    assert item.needs_review is True
    # The key win: no GPS, but still timeline-visible and linked.
    assert item.taken_at is not None
    assert item.taken_at_source == TimeSource.SIDECAR
    assert item.google_photos_url == "https://photos.google.com/photo/BBB"


def test_orphan_photo_gets_mtime(db, thumbs_dir, takeout):
    _pipeline(db, thumbs_dir).ingest_directory(takeout)
    item = _by_name(db, "IMG_3.JPG")
    assert item.taken_at is not None
    assert item.taken_at_source == TimeSource.FILE_MTIME
    assert item.needs_review is True


def test_thumbnails_cached_eagerly(db, thumbs_dir, takeout):
    pipeline = _pipeline(db, thumbs_dir, generate_thumbnails=True)  # opt-in
    pipeline.ingest_directory(takeout)
    for item in _all(db):
        assert item.thumbnail_cached_at is not None
        assert pipeline.thumbnails.exists(item.dedupe_key)


def test_thumbnails_optional(db, thumbs_dir, takeout):
    pipeline = _pipeline(db, thumbs_dir, generate_thumbnails=False)
    pipeline.ingest_directory(takeout)
    # Items still fully ingested (time + location), just no thumbnails.
    assert db.count_media() == 3
    located = _by_name(db, "IMG_1.JPG")
    assert located.has_location and located.taken_at is not None
    for item in _all(db):
        assert item.thumbnail_cached_at is None
        assert not pipeline.thumbnails.exists(item.dedupe_key)
    # And re-ingest still skips (completeness doesn't require a thumbnail here).
    stats = _pipeline(db, thumbs_dir, generate_thumbnails=False).ingest_directory(takeout)
    assert stats.skipped == 3


def test_limit_processes_only_a_subset(db, thumbs_dir, takeout):
    stats = _pipeline(db, thumbs_dir).ingest_directory(takeout, limit=2)
    assert stats.total_files == 2
    assert stats.created == 2
    assert db.count_media() == 2


def test_idempotent_reingest(db, thumbs_dir, takeout):
    _pipeline(db, thumbs_dir).ingest_directory(takeout)
    stats = _pipeline(db, thumbs_dir).ingest_directory(takeout)
    assert db.count_media() == 3
    assert stats.created == 0
    assert stats.skipped == 3


def test_manual_location_preserved_on_reingest(db, thumbs_dir, takeout):
    _pipeline(db, thumbs_dir).ingest_directory(takeout)
    item = _by_name(db, "IMG_2.JPG")
    item.set_location(1.29, 103.85, source=LocationSource.MANUAL)
    item.needs_review = False
    db.upsert_media(item)

    _pipeline(db, thumbs_dir).ingest_directory(takeout, force=True)
    item = _by_name(db, "IMG_2.JPG")
    assert item.location_source == LocationSource.MANUAL
    assert item.latitude == pytest.approx(1.29)


def test_exif_time_localized_with_tz():
    # 14:30 JST (Asia/Tokyo, UTC+9) -> 05:30 UTC
    dt, src = _resolve_taken_at(None, "2019:08:04 14:30:00", Path("/no/such"), tz="Asia/Tokyo")
    assert src == TimeSource.EXIF
    assert dt == datetime(2019, 8, 4, 5, 30, tzinfo=dt_timezone.utc)


def test_exif_time_utc_fallback_without_tz():
    dt, src = _resolve_taken_at(None, "2019:08:04 14:30:00", Path("/no/such"), tz=None)
    assert dt == datetime(2019, 8, 4, 14, 30, tzinfo=dt_timezone.utc)


def test_dedupe_key_stable_across_paths():
    # Same Google item, different export paths -> same key.
    k1 = compute_dedupe_key(
        google_url="https://photos.google.com/photo/X",
        title="a.jpg",
        epoch=1,
        datetime_text=None,
        perceptual_hash=None,
        file_name="a.jpg",
        file_size=10,
    )
    k2 = compute_dedupe_key(
        google_url="https://photos.google.com/photo/X",
        title="a.jpg",
        epoch=1,
        datetime_text=None,
        perceptual_hash=None,
        file_name="a.jpg",
        file_size=999,
    )
    assert k1 == k2
