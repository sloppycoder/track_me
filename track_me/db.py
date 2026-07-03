"""SQLite data layer (no ORM).

A thin repository over stdlib ``sqlite3``: ``Media`` and ``Place`` dataclasses
plus a ``Database`` wrapper. Datetimes are stored as ISO-8601 UTC text; JSON
blobs (``sidecar_raw``/``exif`` on media, ``geocode_raw`` on place) as text.

    db = Database(config.DB_PATH)
    db.init_schema()
    db.upsert_media(Media(dedupe_key="abc", file_name="a.jpg"))
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import h3

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# res-11 H3 cell persisted per photo; coarser cells derived with cell_to_parent.
H3_BASE_RESOLUTION = 11

# media columns in schema order, excluding the autoincrement `id`.
_MEDIA_COLUMNS = (
    "dedupe_key",
    "google_photos_url",
    "file_name",
    "kind",
    "source_path",
    "taken_at",
    "taken_at_source",
    "timezone",
    "local_date",
    "latitude",
    "longitude",
    "h3_cell",
    "geo_cell",
    "location_source",
    "sidecar_raw",
    "exif",
    "thumbnail_cached_at",
    "perceptual_hash",
    "needs_review",
    "created_at",
    "updated_at",
)
_PLACE_COLUMNS = (
    "h3_cell",
    "center_lat",
    "center_lng",
    "city",
    "admin1",
    "country_code",
    "formatted_address",
    "geocode_raw",
    "geocoded_at",
)


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #
def now_utc() -> datetime:
    return datetime.now(UTC)


def to_iso(dt: datetime | None) -> str | None:
    """Serialize a datetime to ISO-8601 UTC text (naive assumed UTC)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def from_iso(text: str | None) -> datetime | None:
    if not text:
        return None
    dt = datetime.fromisoformat(text)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def local_date_for(taken_at: datetime | None, tz: str | None) -> str | None:
    """The photo's LOCAL calendar day ('YYYY-MM-DD'), for timeline bucketing."""
    if taken_at is None:
        return None
    if tz:
        try:
            return taken_at.astimezone(ZoneInfo(tz)).date().isoformat()
        except Exception:
            pass
    return taken_at.astimezone(UTC).date().isoformat()


# --------------------------------------------------------------------------- #
# dataclasses                                                                  #
# --------------------------------------------------------------------------- #
@dataclass
class Place:
    """One geocoded location cell (shared across photos in the cell)."""

    h3_cell: str
    center_lat: float | None = None
    center_lng: float | None = None
    city: str | None = None
    admin1: str | None = None
    country_code: str | None = None
    formatted_address: str | None = None
    geocode_raw: dict | list | None = None
    geocoded_at: datetime | None = None


@dataclass
class Media:
    """One photo/video row."""

    dedupe_key: str
    google_photos_url: str | None = None
    file_name: str = ""
    kind: str = "photo"
    source_path: str | None = None

    taken_at: datetime | None = None
    taken_at_source: str | None = None
    timezone: str | None = None
    local_date: str | None = None

    latitude: float | None = None
    longitude: float | None = None
    h3_cell: str | None = None
    geo_cell: str | None = None
    location_source: str = "none"

    sidecar_raw: dict | None = None
    exif: dict | None = None

    thumbnail_cached_at: datetime | None = None
    perceptual_hash: str | None = None

    needs_review: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None

    id: int | None = field(default=None)

    # --- helpers ---------------------------------------------------------
    @property
    def has_location(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    def set_location(self, lat: float, lon: float, source: str) -> None:
        """Set coordinates, recompute the res-11 H3 cell, tag the source."""
        self.latitude = float(lat)
        self.longitude = float(lon)
        self.h3_cell = h3.latlng_to_cell(lat, lon, H3_BASE_RESOLUTION)
        self.location_source = source

    def clear_location(self) -> None:
        self.latitude = self.longitude = self.h3_cell = self.geo_cell = None
        self.location_source = "none"

    def refresh_local_date(self) -> None:
        self.local_date = local_date_for(self.taken_at, self.timezone)

    def geo_cell_at(self, resolution: int) -> str | None:
        """The parent H3 cell at a coarser resolution (the geocode cell)."""
        if not self.h3_cell:
            return None
        if resolution >= H3_BASE_RESOLUTION:
            return self.h3_cell
        return h3.cell_to_parent(self.h3_cell, resolution)


# --------------------------------------------------------------------------- #
# row <-> dataclass                                                            #
# --------------------------------------------------------------------------- #
def _media_to_params(m: Media) -> dict:
    return {
        "dedupe_key": m.dedupe_key,
        "google_photos_url": m.google_photos_url,
        "file_name": m.file_name,
        "kind": m.kind,
        "source_path": m.source_path,
        "taken_at": to_iso(m.taken_at),
        "taken_at_source": m.taken_at_source,
        "timezone": m.timezone,
        "local_date": m.local_date,
        "latitude": m.latitude,
        "longitude": m.longitude,
        "h3_cell": m.h3_cell,
        "geo_cell": m.geo_cell,
        "location_source": m.location_source,
        "sidecar_raw": json.dumps(m.sidecar_raw, ensure_ascii=False) if m.sidecar_raw else None,
        "exif": json.dumps(m.exif, ensure_ascii=False) if m.exif else None,
        "thumbnail_cached_at": to_iso(m.thumbnail_cached_at),
        "perceptual_hash": m.perceptual_hash,
        "needs_review": int(m.needs_review),
        "created_at": to_iso(m.created_at),
        "updated_at": to_iso(m.updated_at),
    }


def _media_from_row(row: sqlite3.Row) -> Media:
    return Media(
        id=row["id"],
        dedupe_key=row["dedupe_key"],
        google_photos_url=row["google_photos_url"],
        file_name=row["file_name"],
        kind=row["kind"],
        source_path=row["source_path"],
        taken_at=from_iso(row["taken_at"]),
        taken_at_source=row["taken_at_source"],
        timezone=row["timezone"],
        local_date=row["local_date"],
        latitude=row["latitude"],
        longitude=row["longitude"],
        h3_cell=row["h3_cell"],
        geo_cell=row["geo_cell"],
        location_source=row["location_source"],
        sidecar_raw=json.loads(row["sidecar_raw"]) if row["sidecar_raw"] else None,
        exif=json.loads(row["exif"]) if row["exif"] else None,
        thumbnail_cached_at=from_iso(row["thumbnail_cached_at"]),
        perceptual_hash=row["perceptual_hash"],
        needs_review=bool(row["needs_review"]),
        created_at=from_iso(row["created_at"]),
        updated_at=from_iso(row["updated_at"]),
    )


def _place_to_params(p: Place) -> dict:
    return {
        "h3_cell": p.h3_cell,
        "center_lat": p.center_lat,
        "center_lng": p.center_lng,
        "city": p.city,
        "admin1": p.admin1,
        "country_code": p.country_code,
        "formatted_address": p.formatted_address,
        "geocode_raw": json.dumps(p.geocode_raw, ensure_ascii=False)
        if p.geocode_raw is not None
        else None,
        "geocoded_at": to_iso(p.geocoded_at),
    }


def _place_from_row(row: sqlite3.Row) -> Place:
    return Place(
        h3_cell=row["h3_cell"],
        center_lat=row["center_lat"],
        center_lng=row["center_lng"],
        city=row["city"],
        admin1=row["admin1"],
        country_code=row["country_code"],
        formatted_address=row["formatted_address"],
        geocode_raw=json.loads(row["geocode_raw"]) if row["geocode_raw"] else None,
        geocoded_at=from_iso(row["geocoded_at"]),
    )


# --------------------------------------------------------------------------- #
# Database                                                                     #
# --------------------------------------------------------------------------- #
class Database:
    """Thin repository over a SQLite connection."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA_PATH.read_text())
        self.conn.commit()

    # --- media -----------------------------------------------------------
    def get_media_by_dedupe_key(self, key: str) -> Media | None:
        row = self.conn.execute("SELECT * FROM media WHERE dedupe_key = ?", (key,)).fetchone()
        return _media_from_row(row) if row else None

    def upsert_media(self, m: Media) -> Media:
        """Insert or update by dedupe_key. Stamps created_at/updated_at and
        keeps local_date in sync; created_at is preserved on update."""
        now = now_utc()
        if m.created_at is None:
            m.created_at = now
        m.updated_at = now
        if m.local_date is None and m.taken_at is not None:
            m.refresh_local_date()

        params = _media_to_params(m)
        cols = ", ".join(_MEDIA_COLUMNS)
        placeholders = ", ".join(f":{c}" for c in _MEDIA_COLUMNS)
        updates = ", ".join(
            f"{c} = excluded.{c}" for c in _MEDIA_COLUMNS if c not in ("dedupe_key", "created_at")
        )
        self.conn.execute(
            f"INSERT INTO media ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(dedupe_key) DO UPDATE SET {updates}",
            params,
        )
        self.conn.commit()
        got = self.get_media_by_dedupe_key(m.dedupe_key)
        assert got is not None
        return got

    def set_geo_cell(self, dedupe_key: str, geo_cell: str | None) -> None:
        self.conn.execute(
            "UPDATE media SET geo_cell = ?, updated_at = ? WHERE dedupe_key = ?",
            (geo_cell, to_iso(now_utc()), dedupe_key),
        )
        self.conn.commit()

    def iter_located(self, year: int | None = None) -> list[Media]:
        """Located items with a timestamp, ordered by absolute UTC time.

        ``year`` filters on the photo's LOCAL year (via ``local_date``)."""
        sql = "SELECT * FROM media WHERE latitude IS NOT NULL AND taken_at IS NOT NULL "
        params: list = []
        if year is not None:
            sql += "AND substr(local_date, 1, 4) = ? "
            params.append(str(year))
        sql += "ORDER BY taken_at"
        return [_media_from_row(r) for r in self.conn.execute(sql, params)]

    def media_pending_geocode(self, recalculate: bool = False) -> list[Media]:
        """Located items whose geo_cell is not yet set (or all located)."""
        sql = "SELECT * FROM media WHERE latitude IS NOT NULL "
        if not recalculate:
            sql += "AND geo_cell IS NULL "
        sql += "ORDER BY taken_at"
        return [_media_from_row(r) for r in self.conn.execute(sql)]

    def count_media(self, located_only: bool = False) -> int:
        sql = "SELECT COUNT(*) FROM media"
        if located_only:
            sql += " WHERE latitude IS NOT NULL"
        return int(self.conn.execute(sql).fetchone()[0])

    # --- place -----------------------------------------------------------
    def get_place(self, h3_cell: str) -> Place | None:
        row = self.conn.execute("SELECT * FROM place WHERE h3_cell = ?", (h3_cell,)).fetchone()
        return _place_from_row(row) if row else None

    def upsert_place(self, p: Place) -> None:
        params = _place_to_params(p)
        cols = ", ".join(_PLACE_COLUMNS)
        placeholders = ", ".join(f":{c}" for c in _PLACE_COLUMNS)
        updates = ", ".join(f"{c} = excluded.{c}" for c in _PLACE_COLUMNS if c != "h3_cell")
        self.conn.execute(
            f"INSERT INTO place ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(h3_cell) DO UPDATE SET {updates}",
            params,
        )
        self.conn.commit()

    def places_pending_derive(self, redo: bool = False) -> list[Place]:
        """Places with a fetched raw response whose city hasn't been derived."""
        sql = "SELECT * FROM place WHERE geocode_raw IS NOT NULL "
        if not redo:
            sql += "AND city IS NULL "
        return [_place_from_row(r) for r in self.conn.execute(sql)]

    def update_place_derived(self, h3_cell: str, city: str | None, admin1: str | None) -> None:
        self.conn.execute(
            "UPDATE place SET city = ?, admin1 = ? WHERE h3_cell = ?",
            (city, admin1, h3_cell),
        )
        self.conn.commit()

    # --- join (timeline / export) ---------------------------------------
    def located_with_place(self, year: int | None = None) -> list[dict]:
        """Located items joined to their place names, ordered by time.

        Returns plain dicts with the fields timeline/export consume."""
        sql = (
            "SELECT m.taken_at, m.local_date, m.latitude, m.longitude, m.timezone, "
            "m.google_photos_url, p.city, p.admin1, p.country_code, p.formatted_address "
            "FROM media m LEFT JOIN place p ON m.geo_cell = p.h3_cell "
            "WHERE m.latitude IS NOT NULL AND m.taken_at IS NOT NULL "
        )
        params: list = []
        if year is not None:
            sql += "AND substr(m.local_date, 1, 4) = ? "
            params.append(str(year))
        sql += "ORDER BY m.taken_at"
        return [dict(r) for r in self.conn.execute(sql, params)]


# guard against dataclass/columns drift
assert {f.name for f in fields(Media)} == set(_MEDIA_COLUMNS) | {"id"}
assert {f.name for f in fields(Place)} == set(_PLACE_COLUMNS)
