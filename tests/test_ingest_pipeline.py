"""End-to-end ingestion tests against a synthetic Takeout tree."""

import json
from datetime import datetime
from datetime import timezone as dt_timezone

import pytest
from PIL import Image

from track_me.db import Database, LocationSource, Media, Place, TimeSource
from track_me.ingest.pipeline import (
    IngestPipeline,
    _dir_year,
    _filter_years,
    _resolve_taken_at,
    compute_dedupe_key,
)

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
    stats = _pipeline(db, thumbs_dir).ingest(takeout)
    assert stats.total_files == 3
    assert stats.created == 3
    assert db.count_media() == 3


def test_located_photo(db, thumbs_dir, takeout):
    _pipeline(db, thumbs_dir).ingest(takeout)
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
    _pipeline(db, thumbs_dir).ingest(takeout)
    item = _by_name(db, "IMG_2.JPG")
    assert not item.has_location
    assert item.location_source == LocationSource.NONE
    assert item.needs_review is True
    # The key win: no GPS, but still timeline-visible and linked.
    assert item.taken_at is not None
    assert item.taken_at_source == TimeSource.SIDECAR
    assert item.google_photos_url == "https://photos.google.com/photo/BBB"


def test_orphan_photo_gets_mtime(db, thumbs_dir, takeout):
    _pipeline(db, thumbs_dir).ingest(takeout)
    item = _by_name(db, "IMG_3.JPG")
    assert item.taken_at is not None
    assert item.taken_at_source == TimeSource.FILE_MTIME
    assert item.needs_review is True


def test_thumbnails_cached_eagerly(db, thumbs_dir, takeout):
    pipeline = _pipeline(db, thumbs_dir, generate_thumbnails=True)  # opt-in
    pipeline.ingest(takeout)
    for item in _all(db):
        assert item.thumbnail_cached_at is not None
        assert pipeline.thumbnails.exists(item.dedupe_key)


def test_thumbnails_optional(db, thumbs_dir, takeout):
    pipeline = _pipeline(db, thumbs_dir, generate_thumbnails=False)
    pipeline.ingest(takeout)
    # Items still fully ingested (time + location), just no thumbnails.
    assert db.count_media() == 3
    located = _by_name(db, "IMG_1.JPG")
    assert located.has_location and located.taken_at is not None
    for item in _all(db):
        assert item.thumbnail_cached_at is None
        assert not pipeline.thumbnails.exists(item.dedupe_key)
    # And re-ingest still skips (completeness doesn't require a thumbnail here).
    stats = _pipeline(db, thumbs_dir, generate_thumbnails=False).ingest(takeout)
    assert stats.skipped == 3


def test_filter_selects_capture_month(db, thumbs_dir, takeout):
    # The two sidecar photos are 2019-08; the orphan's mtime is "now" (out of range).
    stats = _pipeline(db, thumbs_dir).ingest(takeout, date_filter=("2019-08", "2019-08"))
    assert stats.total_files == 3  # discovery is unaffected
    assert stats.created == 2
    assert stats.filtered == 1  # orphan excluded
    assert db.count_media() == 2


def test_filter_excludes_everything_out_of_range(db, thumbs_dir, takeout):
    stats = _pipeline(db, thumbs_dir).ingest(takeout, date_filter=("2010-01", "2010-12"))
    assert db.count_media() == 0
    assert stats.filtered == 3
    assert stats.created == 0


def _pipeline_with_log(db, thumbs_dir, msgs):
    return IngestPipeline(
        db,
        thumbnail_cache_dir=thumbs_dir,
        thumbnail_size=(100, 100),
        progress_callback=msgs.append,
    )


def test_year_dir_prefilter_skips_nonmatching_years(db, thumbs_dir, takeout):
    # Fixture photos live in 'Photos from 2019'. A 2022 filter is outside the
    # ±1-widened year window {2021,2022,2023}, so the whole folder is dropped by
    # the zero-I/O directory pre-filter -- no sidecars read.
    msgs: list[str] = []
    stats = _pipeline_with_log(db, thumbs_dir, msgs).ingest(
        takeout, date_filter=("2022-06", "2022-06")
    )
    assert stats.created == 0
    assert stats.filtered == 3
    assert db.count_media() == 0
    assert any("Pre-filtered 3" in m for m in msgs)


def test_year_dir_prefilter_keeps_adjacent_year(db, thumbs_dir, takeout):
    # A 2020-01 filter keeps 'Photos from 2019' (±1 window {2019,2020,2021}) so a
    # New-Year photo mis-shelved by a year isn't wrongly dropped; the real 2019-08
    # photos are then excluded by the fine-grained month check, not the folder.
    msgs: list[str] = []
    stats = _pipeline_with_log(db, thumbs_dir, msgs).ingest(
        takeout, date_filter=("2020-01", "2020-01")
    )
    assert stats.created == 0
    assert stats.filtered == 3
    assert not any("Pre-filtered" in m for m in msgs)  # folder kept, excluded by month


def test_non_year_folder_always_scanned(db, thumbs_dir, tmp_path):
    # A folder that isn't 'Photos from YYYY' can't be pre-filtered by year, so it's
    # always scanned regardless of the requested range.
    root = tmp_path / "Takeout" / "Google Photos" / "Me"
    root.mkdir(parents=True)
    _jpeg(root / "IMG_1.JPG")
    _sidecar(
        root / "IMG_1.JPG.json",
        title="IMG_1.JPG",
        url="https://photos.google.com/photo/AAA",
        photoTakenTime={"timestamp": "1564920000"},  # 2019-08-04
        geoData={"latitude": TOKYO[0], "longitude": TOKYO[1]},
    )
    src = str(tmp_path / "Takeout")
    # Far-off year filter: scanned, then excluded by the fine-grained month check.
    stats = _pipeline(db, thumbs_dir).ingest(src, date_filter=("2022-06", "2022-06"))
    assert stats.filtered == 1
    assert stats.created == 0
    # Matching month: ingested normally.
    stats = _pipeline(db, thumbs_dir).ingest(src, date_filter=("2019-08", "2019-08"))
    assert stats.created == 1


def test_filter_years_widens_by_one():
    assert _filter_years(None) is None
    assert _filter_years(("2012-03", "2012-03")) == {2011, 2012, 2013}
    assert _filter_years(("2012-11", "2013-02")) == {2011, 2012, 2013, 2014}


def test_dir_year_parses_photos_from():
    assert _dir_year("Takeout/Google Photos/Photos from 2019") == 2019
    assert _dir_year("Takeout/Google Photos/Me") is None
    assert _dir_year("") is None


def test_idempotent_reingest(db, thumbs_dir, takeout):
    _pipeline(db, thumbs_dir).ingest(takeout)
    stats = _pipeline(db, thumbs_dir).ingest(takeout)
    assert db.count_media() == 3
    assert stats.created == 0
    assert stats.skipped == 3


def test_manual_location_preserved_on_reingest(db, thumbs_dir, takeout):
    _pipeline(db, thumbs_dir).ingest(takeout)
    item = _by_name(db, "IMG_2.JPG")
    item.set_location(1.29, 103.85, source=LocationSource.MANUAL)
    item.needs_review = False
    db.upsert_media(item)

    _pipeline(db, thumbs_dir).ingest(takeout, force=True)
    item = _by_name(db, "IMG_2.JPG")
    assert item.location_source == LocationSource.MANUAL
    assert item.latitude == pytest.approx(1.29)


def test_retagged_sidecar_is_refreshed_not_skipped(db, thumbs_dir, tmp_path):
    root = tmp_path / "T"
    root.mkdir()
    _jpeg(root / "P.JPG")
    _sidecar(  # imported untagged (no geoData)
        root / "P.JPG.json",
        title="P.JPG",
        url="https://photos.google.com/photo/P",
        photoTakenTime={"timestamp": "1564920000"},
    )
    _pipeline(db, thumbs_dir).ingest(str(root))
    assert not _by_name(db, "P.JPG").has_location

    # user tags location in Google Photos, re-exports -> sidecar now has geoData
    _sidecar(
        root / "P.JPG.json",
        title="P.JPG",
        url="https://photos.google.com/photo/P",
        photoTakenTime={"timestamp": "1564920000"},
        geoData={"latitude": 48.857, "longitude": 2.352},
    )
    stats = _pipeline(db, thumbs_dir).ingest(str(root))
    assert stats.refreshed == 1
    assert stats.skipped == 0
    item = _by_name(db, "P.JPG")
    assert item.has_location and item.location_source == LocationSource.TAKEOUT


def test_moved_coords_invalidate_geocell(db, thumbs_dir, tmp_path):
    root = tmp_path / "T"
    root.mkdir()
    _jpeg(root / "M.JPG")
    _sidecar(
        root / "M.JPG.json",
        title="M.JPG",
        url="https://photos.google.com/photo/M",
        photoTakenTime={"timestamp": "1564920000"},
        geoData={"latitude": 48.857, "longitude": 2.352},
    )
    _pipeline(db, thumbs_dir).ingest(str(root))
    item = _by_name(db, "M.JPG")
    # pretend geocode linked it to a place cell
    db.upsert_place(Place(h3_cell="cellX", country_code="FR"))
    db.set_geo_cell(item.dedupe_key, "cellX")

    # location moved to Madrid -> different h3 cell -> geo_cell must be invalidated
    _sidecar(
        root / "M.JPG.json",
        title="M.JPG",
        url="https://photos.google.com/photo/M",
        photoTakenTime={"timestamp": "1564920000"},
        geoData={"latitude": 40.4168, "longitude": -3.7038},
    )
    _pipeline(db, thumbs_dir).ingest(str(root))
    assert _by_name(db, "M.JPG").geo_cell is None  # re-queued for geocode


def test_exif_time_localized_with_tz():
    # 14:30 JST (Asia/Tokyo, UTC+9) -> 05:30 UTC
    dt, src = _resolve_taken_at(None, "2019:08:04 14:30:00", None, tz="Asia/Tokyo")
    assert src == TimeSource.EXIF
    assert dt == datetime(2019, 8, 4, 5, 30, tzinfo=dt_timezone.utc)


def test_exif_time_utc_fallback_without_tz():
    dt, src = _resolve_taken_at(None, "2019:08:04 14:30:00", None, tz=None)
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
