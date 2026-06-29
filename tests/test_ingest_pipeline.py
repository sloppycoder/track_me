"""End-to-end ingestion tests against a synthetic Takeout tree."""

import json

import pytest
from PIL import Image

from library.ingest.pipeline import IngestPipeline, compute_dedupe_key
from library.models import LocationSource, MediaItem, TimeSource

TOKYO = (35.6895, 139.6917)


def _jpeg(path, color=(120, 120, 120)):
    Image.new("RGB", (64, 48), color).save(path, "JPEG")


def _sidecar(path, **payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def takeout(tmp_path, settings):
    """A small Takeout-like tree; thumbnails cached under tmp."""
    settings.THUMBNAIL_CACHE_DIR = tmp_path / "thumbs"
    settings.THUMBNAIL_SIZE = (100, 100)
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


@pytest.mark.django_db
def test_ingest_creates_items(takeout):
    stats = IngestPipeline().ingest_directory(takeout)
    assert stats.total_files == 3
    assert stats.created == 3
    assert MediaItem.objects.count() == 3


@pytest.mark.django_db
def test_located_photo(takeout):
    IngestPipeline().ingest_directory(takeout)
    item = MediaItem.objects.get(file_name="IMG_1.JPG")
    assert item.has_location
    assert item.location_source == LocationSource.TAKEOUT
    assert item.time_source == TimeSource.SIDECAR
    assert item.taken_at is not None
    assert item.google_photos_url == "https://photos.google.com/photo/AAA"
    assert item.h3_cell  # computed from coords
    assert not item.needs_review


@pytest.mark.django_db
def test_sidecar_without_location_still_has_time_and_link(takeout):
    IngestPipeline().ingest_directory(takeout)
    item = MediaItem.objects.get(file_name="IMG_2.JPG")
    assert not item.has_location
    assert item.location_source == LocationSource.NONE
    assert item.needs_review is True
    # The key win: no GPS, but still timeline-visible and linked.
    assert item.taken_at is not None
    assert item.time_source == TimeSource.SIDECAR
    assert item.google_photos_url == "https://photos.google.com/photo/BBB"


@pytest.mark.django_db
def test_orphan_photo_gets_mtime(takeout):
    IngestPipeline().ingest_directory(takeout)
    item = MediaItem.objects.get(file_name="IMG_3.JPG")
    assert item.taken_at is not None
    assert item.time_source == TimeSource.FILE_MTIME
    assert item.needs_review is True


@pytest.mark.django_db
def test_thumbnails_cached_eagerly(takeout):
    pipeline = IngestPipeline()
    pipeline.ingest_directory(takeout)
    for item in MediaItem.objects.all():
        assert item.thumbnail_cached_at is not None
        assert pipeline.thumbnails.exists(item.dedupe_key)


@pytest.mark.django_db
def test_idempotent_reingest(takeout):
    IngestPipeline().ingest_directory(takeout)
    stats = IngestPipeline().ingest_directory(takeout)
    assert MediaItem.objects.count() == 3
    assert stats.created == 0
    assert stats.skipped == 3


@pytest.mark.django_db
def test_manual_location_preserved_on_reingest(takeout):
    IngestPipeline().ingest_directory(takeout)
    item = MediaItem.objects.get(file_name="IMG_2.JPG")
    item.set_location(1.29, 103.85, source=LocationSource.MANUAL)
    item.needs_review = False
    item.save()

    IngestPipeline().ingest_directory(takeout, force=True)
    item.refresh_from_db()
    assert item.location_source == LocationSource.MANUAL
    assert float(item.latitude) == pytest.approx(1.29)


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
