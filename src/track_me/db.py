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


# --------------------------------------------------------------------------- #
# Home locations (hard-coded; see schema.sql `home`)                           #
# --------------------------------------------------------------------------- #
HOME_RADIUS_KM = 160.934  # 100 miles: a stay this close to home is not travel


@dataclass(frozen=True)
class Home:
    """A place the user lived over an inclusive date range (``valid_to`` None = open)."""

    label: str
    latitude: float
    longitude: float
    valid_from: str
    valid_to: str | None = None
    radius_km: float = HOME_RADIUS_KM

    def covers(self, day: str) -> bool:
        return self.valid_from <= day and (self.valid_to is None or day <= self.valid_to)


# Source of truth for home periods. Gaps (e.g. 2024-04-04..2026-11-25, "roaming")
# are intentional: with no home, every stay is travel. Dates were fixed from photo
# metadata and the owner's recollection; edit here and re-run init_schema.
HOME_SEED: tuple[Home, ...] = (
    Home("San Jose, CA", 37.3382, -121.8863, "1998-04-11", "2005-10-09"),
    Home("Beijing, CN", 39.9042, 116.4074, "2005-10-10", "2007-01-17"),
    Home("San Jose, CA", 37.3382, -121.8863, "2007-01-18", "2010-12-17"),
    Home("Redmond, WA", 47.6740, -122.1215, "2010-12-18", "2013-07-13"),
    Home("Kuala Lumpur, MY", 3.1390, 101.6869, "2013-07-14", "2016-09-18"),
    Home("Singapore, SG", 1.3521, 103.8198, "2016-09-19", "2017-11-01"),
    Home("Kuala Lumpur, MY", 3.1390, 101.6869, "2017-11-02", "2019-01-27"),
    Home("Singapore, SG", 1.3521, 103.8198, "2019-01-28", "2024-04-03"),
    Home("Bellingham, WA", 48.7519, -122.4787, "2026-11-26", "2027-06-30"),
)


def home_on(homes, day: str | None) -> Home | None:
    """The home active on ISO date ``day``, or None (no home / unknown date)."""
    if not day:
        return None
    return next((h for h in homes if h.covers(day)), None)


# Enum-like string constants (values match the schema CHECK constraints). Plain
# classes rather than Django TextChoices — no framework dependency.
class MediaKind:
    PHOTO = "photo"
    VIDEO = "video"


class TimeSource:
    SIDECAR = "sidecar"
    EXIF = "exif"
    FILE_MTIME = "file_mtime"
    MANUAL = "manual"


class LocationSource:
    EXIF_GPS = "exif_gps"
    TAKEOUT = "takeout_geodata"
    MANUAL = "manual"
    INTERPOLATED = "interpolated"
    NONE = "none"


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
    "sidecar_fingerprint",
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
    sidecar_fingerprint: str | None = None

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
        "sidecar_fingerprint": m.sidecar_fingerprint,
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
        sidecar_fingerprint=row["sidecar_fingerprint"],
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

    @classmethod
    def read_only(cls, path: str | Path, *, immutable: bool = False) -> Database:
        """Open an existing DB read-only — no ``mkdir``, no ``init_schema``, no
        writes (any write raises ``sqlite3.OperationalError``).

        The file must already exist (``mode=ro`` will not create it). Pass
        ``immutable=True`` to also promise SQLite the file won't change while
        open: it then skips all locking and the ``-wal``/``-shm`` sidecars, a
        real win on network filesystems (e.g. gcsfuse) — but only safe if
        nothing mutates the file for the life of the connection.
        """
        self = cls.__new__(cls)
        self.path = Path(path)
        flags = "mode=ro&immutable=1" if immutable else "mode=ro"
        self.conn = sqlite3.connect(f"file:{self.path}?{flags}", uri=True)
        self.conn.row_factory = sqlite3.Row
        return self

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA_PATH.read_text())
        self.conn.execute("DELETE FROM home")
        self.conn.executemany(
            "INSERT INTO home (label, latitude, longitude, radius_km, valid_from, valid_to) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (h.label, h.latitude, h.longitude, h.radius_km, h.valid_from, h.valid_to)
                for h in HOME_SEED
            ],
        )
        self.conn.commit()

    def homes(self) -> list[Home]:
        """Home periods, oldest first. A catalog built before the ``home`` table
        existed (read-only, never re-initialised) falls back to :data:`HOME_SEED`."""
        try:
            rows = self.conn.execute(
                "SELECT label, latitude, longitude, radius_km, valid_from, valid_to "
                "FROM home ORDER BY valid_from"
            ).fetchall()
        except sqlite3.OperationalError:
            return list(HOME_SEED)
        return [
            Home(
                r["label"],
                r["latitude"],
                r["longitude"],
                r["valid_from"],
                r["valid_to"],
                r["radius_km"],
            )
            for r in rows
        ] or list(HOME_SEED)

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

    def set_geo_cells(self, updates: list[tuple[str, str]]) -> None:
        """Bulk-set media.geo_cell from (dedupe_key, geo_cell) pairs, one commit."""
        ts = to_iso(now_utc())
        self.conn.executemany(
            "UPDATE media SET geo_cell = ?, updated_at = ? WHERE dedupe_key = ?",
            [(cell, ts, key) for key, cell in updates],
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

    # --- query (MCP server; read-only) ----------------------------------
    def search_located(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        country: str | None = None,
        city: str | None = None,
    ) -> list[dict]:
        """Located photos joined to their place, ordered by time.

        ``start_date``/``end_date`` are inclusive ``YYYY-MM-DD`` bounds on the
        photo's LOCAL day (``local_date``). ``country`` is an ISO-2 code. ``city``
        matches ``place.city`` or ``place.admin1`` case-insensitively (admin1 is
        the city label for metros such as Tokyo — see timeline.METRO_AS_CITY)."""
        sql = (
            "SELECT m.id, m.file_name, m.kind, m.taken_at, m.local_date, m.timezone, "
            "m.latitude, m.longitude, m.google_photos_url, "
            "p.city, p.admin1, p.country_code, p.formatted_address "
            "FROM media m LEFT JOIN place p ON m.geo_cell = p.h3_cell "
            "WHERE m.latitude IS NOT NULL AND m.taken_at IS NOT NULL "
        )
        params: list = []
        if start_date:
            sql += "AND m.local_date >= ? "
            params.append(start_date)
        if end_date:
            sql += "AND m.local_date <= ? "
            params.append(end_date)
        if country:
            sql += "AND p.country_code = ? "
            params.append(country.upper())
        if city:
            sql += "AND (p.city = ? COLLATE NOCASE OR p.admin1 = ? COLLATE NOCASE) "
            params.extend([city, city])
        sql += "ORDER BY m.taken_at"
        return [dict(r) for r in self.conn.execute(sql, params)]

    def search_places(self, text: str, limit: int = 25) -> list[dict]:
        """Place names (city/admin1/address) containing ``text``, with the photo
        count and local-date span of each (city, admin1, country) group."""
        like = f"%{text}%"
        sql = (
            "SELECT p.city, p.admin1, p.country_code, COUNT(m.id) AS photo_count, "
            "MIN(m.local_date) AS first_date, MAX(m.local_date) AS last_date "
            "FROM place p JOIN media m ON m.geo_cell = p.h3_cell "
            "WHERE p.city LIKE ? OR p.admin1 LIKE ? OR p.formatted_address LIKE ? "
            "GROUP BY p.city, p.admin1, p.country_code "
            "ORDER BY photo_count DESC LIMIT ?"
        )
        return [dict(r) for r in self.conn.execute(sql, (like, like, like, limit))]

    def catalog_summary(self) -> dict:
        """Overall counts, the local-date span, and photo counts per country."""
        row = self.conn.execute(
            "SELECT COUNT(*) AS total, COUNT(latitude) AS located, "
            "MIN(local_date) AS first_date, MAX(local_date) AS last_date FROM media"
        ).fetchone()
        countries = self.conn.execute(
            "SELECT p.country_code, COUNT(*) AS photo_count, "
            "MIN(m.local_date) AS first_date, MAX(m.local_date) AS last_date "
            "FROM media m JOIN place p ON m.geo_cell = p.h3_cell "
            "WHERE p.country_code IS NOT NULL "
            "GROUP BY p.country_code ORDER BY photo_count DESC"
        ).fetchall()
        return {**dict(row), "countries": [dict(r) for r in countries]}


# guard against dataclass/columns drift
assert {f.name for f in fields(Media)} == set(_MEDIA_COLUMNS) | {"id"}
assert {f.name for f in fields(Place)} == set(_PLACE_COLUMNS)
