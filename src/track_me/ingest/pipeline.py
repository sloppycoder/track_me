"""Ingestion orchestration over an object store (local dir or S3).

For each media object: match its Takeout sidecar → resolve identity/time/location
from the **sidecar first**, reading (and decoding) the image only when the sidecar
doesn't supply what's needed. Per-object work runs in a thread pool (I/O-bound on
S3); the main thread is the sole SQLite writer.

Re-runnable and incremental: unchanged items fast-skip by ``dedupe_key`` +
``sidecar_fingerprint``; re-tagged items (new GPS/title) fall through and refresh,
and a moved location invalidates its stale geocode link. Manual edits are never
overwritten.
"""

from __future__ import annotations

import hashlib
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from datetime import timezone as dt_timezone

from track_me import config
from track_me.db import Database, LocationSource, Media, MediaKind, TimeSource, from_iso, now_utc
from track_me.ingest import exif as exif_mod
from track_me.ingest import sidecar as sidecar_mod
from track_me.ingest.matcher import SidecarMatcher
from track_me.ingest.sidecar import Sidecar
from track_me.storage import ObjectInfo, ObjectStore, from_uri
from track_me.thumbnails import ThumbnailService
from track_me.tz import timezone_for as tz_for

logger = logging.getLogger(__name__)

DEFAULT_WORKERS = 32


@dataclass
class IngestStats:
    total_files: int = 0
    created: int = 0
    updated: int = 0
    refreshed: int = 0
    skipped: int = 0
    filtered: int = 0
    errors: int = 0
    with_location: int = 0
    without_location: int = 0
    with_sidecar: int = 0
    error_details: list[str] = field(default_factory=list)


@dataclass
class _Prev:
    """Lightweight snapshot of an already-ingested row (read-only in workers)."""

    taken_at: datetime | None
    taken_at_source: str | None
    timezone: str | None
    latitude: float | None
    longitude: float | None
    h3_cell: str | None
    geo_cell: str | None
    location_source: str
    needs_review: bool
    thumbnail_cached_at: datetime | None
    created_at: datetime | None
    sidecar_fingerprint: str | None


@dataclass
class _Result:
    action: str  # created | updated | refreshed | skipped | filtered | error
    media: Media | None = None
    with_location: bool = False
    with_sidecar: bool = False
    error: str | None = None


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
    """Stable identity, strongest signal first."""
    if google_url:
        return _sha1(["url", google_url.strip()])
    if title and epoch is not None:
        return _sha1(["title-time", title.lower(), str(epoch)])
    if datetime_text and perceptual_hash:
        return _sha1(["exif-hash", datetime_text, perceptual_hash])
    return _sha1(["file", file_name.lower(), str(file_size)])


def compute_sidecar_fingerprint(sidecar: Sidecar | None) -> str | None:
    """Hash the *significant* sidecar fields (coords, title, description) so
    re-tagging is detected on re-import. None for orphans (no sidecar)."""
    if sidecar is None:
        return None
    coords = sidecar.coords()
    parts = [
        (sidecar.title or "").strip().lower(),
        (sidecar.description or "").strip().lower(),
        f"{coords[0]:.6f},{coords[1]:.6f}" if coords else "",
    ]
    return _sha1(["sidecar", *parts])


def _resolve_taken_at(
    sidecar: Sidecar | None,
    datetime_text: str | None,
    mtime: datetime | None,
    tz: str | None = None,
) -> tuple[datetime | None, str | None]:
    """Capture time for EVERY item: sidecar epoch -> EXIF -> object mtime.

    Sidecar epoch and mtime are absolute UTC instants. EXIF DateTimeOriginal is a
    naive LOCAL wall-clock, localized via the location's zone when known."""
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

    if mtime is not None:
        return mtime, TimeSource.FILE_MTIME
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
        workers: int = DEFAULT_WORKERS,
    ):
        self.db = db or Database(config.DB_PATH)
        self.db.init_schema()
        self.progress = progress_callback or (lambda _m: None)
        self.generate_thumbnails = generate_thumbnails
        self.workers = workers
        self.thumbnails = ThumbnailService(
            cache_dir=thumbnail_cache_dir or config.THUMBNAIL_CACHE_DIR,
            size=thumbnail_size or config.THUMBNAIL_SIZE,
        )
        self._date_filter: tuple[str, str] | None = None

    # --- public API ------------------------------------------------------
    def ingest(
        self,
        source: str,
        *,
        force: bool = False,
        date_filter: tuple[str, str] | None = None,
        workers: int | None = None,
    ) -> IngestStats:
        """Ingest from ``source`` (a local path or ``s3://bucket/prefix`` URI).

        ``date_filter`` = an inclusive ('YYYY-MM','YYYY-MM') capture-month range."""
        self._date_filter = date_filter
        store, prefix = from_uri(source)

        files_by_dir: dict[str, list[str]] = {}
        media: list[ObjectInfo] = []
        for obj in store.list(prefix):
            directory, _, name = obj.key.rpartition("/")
            files_by_dir.setdefault(directory, []).append(name)
            if _kind_for(os.path.splitext(name)[1].lower()) is not None:
                media.append(obj)

        stats = IngestStats()
        stats.total_files = len(media)
        suffix = f" (filter {date_filter[0]}..{date_filter[1]})" if date_filter else ""
        self.progress(f"Found {stats.total_files} media objects under {source}{suffix}")

        matcher = SidecarMatcher(store, files_by_dir)
        prev_by_key = self._load_prev()
        n = workers or self.workers

        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = [
                pool.submit(self._resolve, obj, store, matcher, prev_by_key, force)
                for obj in media
            ]
            for fut in as_completed(futures):
                self._apply(fut.result(), stats)

        return stats

    # --- main-thread DB writes -------------------------------------------
    def _load_prev(self) -> dict[str, _Prev]:
        prev: dict[str, _Prev] = {}
        for r in self.db.conn.execute(
            "SELECT dedupe_key, taken_at, taken_at_source, timezone, latitude, longitude, "
            "h3_cell, geo_cell, location_source, needs_review, thumbnail_cached_at, "
            "created_at, sidecar_fingerprint FROM media"
        ):
            prev[r["dedupe_key"]] = _Prev(
                taken_at=from_iso(r["taken_at"]),
                taken_at_source=r["taken_at_source"],
                timezone=r["timezone"],
                latitude=r["latitude"],
                longitude=r["longitude"],
                h3_cell=r["h3_cell"],
                geo_cell=r["geo_cell"],
                location_source=r["location_source"],
                needs_review=bool(r["needs_review"]),
                thumbnail_cached_at=from_iso(r["thumbnail_cached_at"]),
                created_at=from_iso(r["created_at"]),
                sidecar_fingerprint=r["sidecar_fingerprint"],
            )
        return prev

    def _apply(self, res: _Result, stats: IngestStats) -> None:
        if res.with_sidecar:
            stats.with_sidecar += 1
        if res.action == "filtered":
            stats.filtered += 1
            return
        if res.action == "skipped":
            stats.skipped += 1
            return
        if res.action == "error":
            stats.errors += 1
            if res.error:
                stats.error_details.append(res.error)
            return
        assert res.media is not None
        self.db.upsert_media(res.media)
        setattr(stats, res.action, getattr(stats, res.action) + 1)
        if res.with_location:
            stats.with_location += 1
        else:
            stats.without_location += 1
        written = stats.created + stats.updated + stats.refreshed
        if written % 100 == 0:
            self.progress(f"Inserted {written} photos...")

    # --- worker (no DB access) -------------------------------------------
    def _resolve(
        self,
        obj: ObjectInfo,
        store: ObjectStore,
        matcher: SidecarMatcher,
        prev_by_key: dict[str, _Prev],
        force: bool,
    ) -> _Result:
        try:
            return self._resolve_inner(obj, store, matcher, prev_by_key, force)
        except Exception as e:  # never let one bad object abort the run
            logger.exception("Ingest failed for %s", obj.key)
            return _Result("error", error=f"{obj.key}: {e}")

    def _resolve_inner(self, obj, store, matcher, prev_by_key, force) -> _Result:
        name = obj.key.rpartition("/")[2]
        kind = _kind_for(os.path.splitext(name)[1].lower())
        assert kind is not None

        sidecar_key = matcher.find(obj.key)
        parsed = matcher.read_json(sidecar_key) if sidecar_key else None
        sc = sidecar_mod.parse_sidecar(parsed)
        with_sidecar = sc is not None

        google_url = sc.url if sc else None
        title = sc.title if sc else None
        epoch = sc.taken_epoch() if sc else None
        fingerprint = compute_sidecar_fingerprint(sc)
        have_cheap_identity = bool(google_url or (title and epoch is not None))

        # Cheap month filter (sidecar epoch) — skip before any image read.
        if self._date_filter and epoch is not None:
            if not self._in_filter(datetime.fromtimestamp(epoch, tz=dt_timezone.utc)):
                return _Result("filtered", with_sidecar=with_sidecar)

        # Cheap fast-skip: identity known from the sidecar and prev is complete +
        # unchanged. Avoids reading the image entirely.
        if have_cheap_identity:
            cheap_key = compute_dedupe_key(
                google_url=google_url,
                title=title,
                epoch=epoch,
                datetime_text=None,
                perceptual_hash=None,
                file_name=name,
                file_size=0,
            )
            prev = prev_by_key.get(cheap_key)
            if not force and prev and prev.taken_at and prev.sidecar_fingerprint == fingerprint:
                if (
                    not self.generate_thumbnails
                    or kind != MediaKind.PHOTO
                    or self.thumbnails.exists(cheap_key)
                ):
                    return _Result("skipped", with_sidecar=with_sidecar)

        # Decide whether the image bytes are needed at all.
        sc_coords = sc.coords() if sc else None
        need_location = sc_coords is None
        need_time = epoch is None
        need_identity = not have_cheap_identity
        need_image = kind == MediaKind.PHOTO and (need_location or need_time or need_identity)

        raw: bytes | None = None
        data = exif_mod.ExifData()
        if need_image:
            raw = store.read(obj.key)
            data = exif_mod.read_exif(raw, with_hash=need_identity)

        key = compute_dedupe_key(
            google_url=google_url,
            title=title,
            epoch=epoch,
            datetime_text=data.datetime_text,
            perceptual_hash=data.perceptual_hash,
            file_name=name,
            file_size=obj.size,
        )
        prev = prev_by_key.get(key)

        # Post-resolve skip: covers orphans (no cheap-identity path) and any item
        # that had to read the image but is complete + unchanged.
        if not force and prev and prev.taken_at and prev.sidecar_fingerprint == fingerprint:
            if (
                not self.generate_thumbnails
                or kind != MediaKind.PHOTO
                or self.thumbnails.exists(key)
            ):
                return _Result("skipped", with_sidecar=with_sidecar)

        # Location: sidecar first, then EXIF GPS.
        if sc_coords is not None:
            coords, loc_source = sc_coords, LocationSource.TAKEOUT
        elif data.coords is not None:
            coords, loc_source = data.coords, LocationSource.EXIF_GPS
        else:
            coords, loc_source = None, LocationSource.NONE
        tz = tz_for(*coords) if coords else None

        taken_at, time_source = _resolve_taken_at(sc, data.datetime_text, obj.last_modified, tz)

        m = Media(dedupe_key=key)
        m.file_name = name
        m.kind = kind
        m.source_path = obj.key[:512]
        if google_url:
            m.google_photos_url = google_url
        m.sidecar_raw = parsed if with_sidecar else None
        m.exif = data.meta or None
        m.perceptual_hash = data.perceptual_hash
        m.sidecar_fingerprint = fingerprint
        m.created_at = prev.created_at if prev else None

        # Time (preserve a MANUAL override).
        if prev and prev.taken_at_source == TimeSource.MANUAL:
            m.taken_at, m.taken_at_source, m.timezone = (
                prev.taken_at,
                TimeSource.MANUAL,
                prev.timezone,
            )
        else:
            m.taken_at, m.taken_at_source = taken_at, time_source
            m.timezone = tz if coords else None

        # Location (preserve MANUAL) + geocode invalidation on a real move.
        if prev and prev.location_source == LocationSource.MANUAL:
            m.latitude, m.longitude = prev.latitude, prev.longitude
            m.h3_cell, m.geo_cell = prev.h3_cell, prev.geo_cell
            m.location_source, m.needs_review = LocationSource.MANUAL, prev.needs_review
        elif coords is not None:
            m.set_location(coords[0], coords[1], source=loc_source)
            m.needs_review = False
            if prev and prev.h3_cell == m.h3_cell:
                m.geo_cell = prev.geo_cell  # unchanged cell -> keep the geocode link
            # else geo_cell stays None -> next `geocode` re-links it
        else:
            m.clear_location()
            m.needs_review = True

        # Thumbnail (opt-in) — reuse already-read bytes when we have them.
        m.thumbnail_cached_at = prev.thumbnail_cached_at if prev else None
        if (
            self.generate_thumbnails
            and kind == MediaKind.PHOTO
            and not self.thumbnails.exists(key)
        ):
            tb = raw if raw is not None else store.read(obj.key)
            if self.thumbnails.generate_from_bytes(tb, key):
                m.thumbnail_cached_at = now_utc()

        m.refresh_local_date()

        # Month filter for files with no sidecar epoch (EXIF/mtime-dated).
        if self._date_filter and epoch is None and not self._in_filter(m.taken_at):
            return _Result("filtered", with_sidecar=with_sidecar)

        if prev is None:
            action = "created"
        elif prev.sidecar_fingerprint != fingerprint:
            action = "refreshed"
        else:
            action = "updated"
        return _Result(action, media=m, with_location=m.has_location, with_sidecar=with_sidecar)

    def _in_filter(self, dt: datetime | None) -> bool:
        if self._date_filter is None:
            return True
        if dt is None:
            return False
        ym = dt.astimezone(dt_timezone.utc).strftime("%Y-%m")
        lo, hi = self._date_filter
        return lo <= ym <= hi
