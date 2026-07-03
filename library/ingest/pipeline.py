"""Ingestion orchestration.

For each media file under a Takeout extract:
  discover -> match+parse sidecar -> read EXIF -> merge (time, location, link)
  -> compute stable dedupe key -> upsert MediaItem -> cache thumbnail eagerly.

Re-runnable and incremental: items already seen (by ``dedupe_key``) are skipped
unless ``force`` is set, and manual edits are never overwritten.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from datetime import timezone as dt_timezone
from pathlib import Path

from library.ingest import exif as exif_mod
from library.ingest.matcher import SidecarMatcher
from library.ingest.sidecar import Sidecar, load_sidecar
from library.media.thumbnails import ThumbnailService
from library.tz import timezone_for as tz_for
from track_me import config
from track_me.db import Database, LocationSource, Media, MediaKind, TimeSource, now_utc

logger = logging.getLogger(__name__)


@dataclass
class IngestStats:
    total_files: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    with_location: int = 0
    without_location: int = 0
    with_sidecar: int = 0
    error_details: list[str] = field(default_factory=list)


def _kind_for(ext: str) -> str | None:
    if ext in exif_mod.IMAGE_EXTENSIONS:
        return MediaKind.PHOTO
    if ext in exif_mod.VIDEO_EXTENSIONS:
        return MediaKind.VIDEO
    return None


def _sha1(parts: list[str]) -> str:
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()


def compute_dedupe_key(
    *,
    google_url: str | None,
    title: str | None,
    epoch: int | None,
    datetime_text: str | None,
    perceptual_hash: str | None,
    file_name: str,
    file_size: int,
) -> str:
    """Stable identity, strongest signal first (see plan)."""
    if google_url:
        return _sha1(["url", google_url.strip()])
    if title and epoch is not None:
        return _sha1(["title-time", title.lower(), str(epoch)])
    if datetime_text and perceptual_hash:
        return _sha1(["exif-hash", datetime_text, perceptual_hash])
    return _sha1(["file", file_name.lower(), str(file_size)])


def _resolve_taken_at(
    sidecar: Sidecar | None,
    datetime_text: str | None,
    file_path: Path,
    tz: str | None = None,
) -> tuple[datetime | None, str | None]:
    """Capture time for EVERY item: sidecar epoch -> EXIF -> file mtime.

    The sidecar timestamp and file mtime are absolute UTC instants. EXIF
    DateTimeOriginal is a naive LOCAL wall-clock, so when the location's zone is
    known we localize it correctly; otherwise we fall back to assuming UTC.
    """
    if sidecar is not None and (epoch := sidecar.taken_epoch()) is not None:
        return datetime.fromtimestamp(epoch, tz=dt_timezone.utc), TimeSource.SIDECAR

    if datetime_text:
        try:
            naive = datetime.strptime(datetime_text.replace(":", "-", 2), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            naive = None
        if naive is not None:
            if tz:
                try:
                    from zoneinfo import ZoneInfo

                    aware = naive.replace(tzinfo=ZoneInfo(tz)).astimezone(dt_timezone.utc)
                    return aware, TimeSource.EXIF
                except Exception:
                    pass
            return naive.replace(tzinfo=dt_timezone.utc), TimeSource.EXIF

    try:
        mtime = file_path.stat().st_mtime
        return datetime.fromtimestamp(mtime, tz=dt_timezone.utc), TimeSource.FILE_MTIME
    except OSError:
        return None, None


class IngestPipeline:
    def __init__(
        self,
        db: Database | None = None,
        *,
        progress_callback=None,
        generate_thumbnails: bool = False,
        thumbnail_cache_dir=None,
        thumbnail_size=None,
    ):
        self.db = db or Database(config.DB_PATH)
        self.db.init_schema()
        self.progress = progress_callback or (lambda _m: None)
        self.generate_thumbnails = generate_thumbnails
        self.matcher = SidecarMatcher()
        self.thumbnails = ThumbnailService(
            cache_dir=thumbnail_cache_dir or config.THUMBNAIL_CACHE_DIR,
            size=thumbnail_size or config.THUMBNAIL_SIZE,
        )

    # --- public API ------------------------------------------------------
    def ingest_directory(
        self, root: str, *, force: bool = False, limit: int | None = None
    ) -> IngestStats:
        stats = IngestStats()
        files = self._discover(root)
        if limit is not None:
            files = files[:limit]
        stats.total_files = len(files)
        suffix = f" (limited to {limit})" if limit is not None else ""
        self.progress(f"Found {stats.total_files} media files under {root}{suffix}")

        for i, path in enumerate(files, start=1):
            try:
                self._ingest_one(path, root, force, stats)
            except Exception as e:  # never let one bad file abort the run
                stats.errors += 1
                stats.error_details.append(f"{path}: {e}")
                logger.exception("Ingest failed for %s", path)
            if i % 200 == 0:
                self.progress(f"Processed {i}/{stats.total_files}")

        return stats

    # --- internals -------------------------------------------------------
    def _discover(self, root: str) -> list[Path]:
        found: list[Path] = []
        for dirpath, _dirs, filenames in os.walk(root):
            for name in filenames:
                ext = os.path.splitext(name)[1].lower()
                if _kind_for(ext) is not None:
                    found.append(Path(dirpath) / name)
        return sorted(found)

    def _ingest_one(self, path: Path, root: str, force: bool, stats: IngestStats) -> None:
        kind = _kind_for(path.suffix.lower())
        assert kind is not None  # _discover only yields recognised media files
        rel_path = os.path.relpath(path, root)

        # Cheap pass: parse sidecar (no image decode yet).
        sidecar_path = self.matcher.find(path)
        sidecar = load_sidecar(sidecar_path) if sidecar_path else None
        if sidecar is not None:
            stats.with_sidecar += 1

        google_url = sidecar.url if sidecar else None
        title = sidecar.title if sidecar else None
        epoch = sidecar.taken_epoch() if sidecar else None

        # Fast skip: if a sidecar-derived key already maps to a complete item.
        if not force and (google_url or (title and epoch is not None)):
            key = compute_dedupe_key(
                google_url=google_url,
                title=title,
                epoch=epoch,
                datetime_text=None,
                perceptual_hash=None,
                file_name=path.name,
                file_size=0,
            )
            existing = self.db.get_media_by_dedupe_key(key)
            if existing and existing.taken_at and self._thumb_ok(existing, kind):
                stats.skipped += 1
                return

        # Full pass: decode the image for EXIF + GPS + hash.
        data = exif_mod.read_exif(path) if kind == MediaKind.PHOTO else exif_mod.ExifData()

        try:
            size = path.stat().st_size
        except OSError:
            size = 0

        key = compute_dedupe_key(
            google_url=google_url,
            title=title,
            epoch=epoch,
            datetime_text=data.datetime_text,
            perceptual_hash=data.perceptual_hash,
            file_name=path.name,
            file_size=size,
        )

        item = self.db.get_media_by_dedupe_key(key)
        created = item is None

        # Orphans (no sidecar) only reveal their key after decoding; once we know
        # it, skip if the item is already complete and unchanged.
        if item is not None and not force and item.taken_at and self._thumb_ok(item, kind):
            stats.skipped += 1
            return

        if item is None:
            item = Media(dedupe_key=key)

        item.file_name = path.name
        item.kind = kind
        item.source_path = rel_path[:512]
        if google_url:
            item.google_photos_url = google_url
        item.sidecar_raw = sidecar.model_dump(exclude_none=True) if sidecar else None
        item.exif = data.meta or None
        item.perceptual_hash = data.perceptual_hash

        # Decide location first so the zone is known before resolving time
        # (EXIF wall-clock needs the local zone to become a correct instant).
        coords, loc_source = self._decide_coords(data, sidecar)
        tz = tz_for(*coords) if coords else None

        # Time (preserve a manual override on re-ingest).
        if not (not created and item.taken_at_source == TimeSource.MANUAL):
            taken_at, time_source = _resolve_taken_at(sidecar, data.datetime_text, path, tz)
            item.taken_at = taken_at
            item.taken_at_source = time_source

        # Location (preserve a manual override on re-ingest).
        if not (not created and item.location_source == LocationSource.MANUAL):
            if coords is not None:
                item.set_location(coords[0], coords[1], source=loc_source)
                item.timezone = tz
                item.needs_review = False
            else:
                item.clear_location()
                item.timezone = None
                item.needs_review = True

        # Eager thumbnail so it survives deletion of the Takeout extract. Generated
        # before the upsert so its timestamp persists in a single write.
        if (
            self.generate_thumbnails
            and kind == MediaKind.PHOTO
            and not self.thumbnails.exists(key)
        ):
            if self.thumbnails.generate(path, key):
                item.thumbnail_cached_at = now_utc()

        item.refresh_local_date()
        self.db.upsert_media(item)

        if item.has_location:
            stats.with_location += 1
        else:
            stats.without_location += 1
        if created:
            stats.created += 1
        else:
            stats.updated += 1

    def _decide_coords(self, data: exif_mod.ExifData, sidecar):
        """Pick coordinates + source: EXIF GPS first, then Takeout geoData."""
        if data.coords is not None:
            return data.coords, LocationSource.EXIF_GPS
        if sidecar is not None and (coords := sidecar.coords()) is not None:
            return coords, LocationSource.TAKEOUT
        return None, LocationSource.NONE

    def _thumb_ok(self, item: Media, kind: str | None) -> bool:
        if not self.generate_thumbnails:
            return True
        return kind != MediaKind.PHOTO or self.thumbnails.exists(item.dedupe_key)
