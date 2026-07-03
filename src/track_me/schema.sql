-- track_me schema (Django-free). Recreate with db.init_schema().
-- Two tables: `place` (one row per geocoded location cell, shared across photos)
-- and `media` (one row per photo/video). media.geo_cell -> place.h3_cell.

PRAGMA foreign_keys = ON;

-- One row per geocoded location cell. Shared by every photo whose geo_cell
-- resolves here, so the Google response is stored once, not per photo.
CREATE TABLE IF NOT EXISTS place (
    h3_cell           TEXT PRIMARY KEY,   -- cell at the active geocode resolution
    center_lat        REAL,
    center_lng        REAL,
    city              TEXT,               -- derived offline from geocode_raw
    admin1            TEXT,               -- state / province / region
    country_code      TEXT,               -- ISO-2
    formatted_address TEXT,               -- Google's full formatted address
    geocode_raw       TEXT,               -- JSON: Google address_components
    geocoded_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_place_country ON place (country_code);
CREATE INDEX IF NOT EXISTS idx_place_city    ON place (city);

-- One row per photo/video. Scan-hot: timeline queries walk this ordered by time.
CREATE TABLE IF NOT EXISTS media (
    id                  INTEGER PRIMARY KEY,
    dedupe_key          TEXT    NOT NULL UNIQUE,
    google_photos_url   TEXT,

    file_name           TEXT    NOT NULL,
    kind                TEXT    NOT NULL DEFAULT 'photo'
                          CHECK (kind IN ('photo', 'video')),
    source_path         TEXT,               -- last-seen Takeout path (info only)

    -- time (UTC, ISO-8601 text; e.g. '2019-06-02T09:14:00+00:00')
    taken_at            TEXT,
    taken_at_source     TEXT    CHECK (taken_at_source IN
                          ('sidecar', 'exif', 'file_mtime', 'manual')),
    timezone            TEXT,               -- IANA zone at the location
    local_date          TEXT,               -- 'YYYY-MM-DD' in local tz (bucketing)

    -- location
    latitude            REAL,
    longitude           REAL,
    h3_cell             TEXT,               -- res 11; coarser cells derived on demand
    geo_cell            TEXT,               -- FK -> place.h3_cell (set by geocode)
    location_source     TEXT    NOT NULL DEFAULT 'none'
                          CHECK (location_source IN
                          ('exif_gps', 'takeout_geodata', 'manual', 'interpolated', 'none')),

    -- per-photo raw metadata (unique per photo -> inline)
    sidecar_raw         TEXT,               -- JSON
    exif                TEXT,               -- JSON
    sidecar_fingerprint TEXT,               -- hash of significant sidecar fields (re-tag detection)

    -- media handling
    thumbnail_cached_at TEXT,
    perceptual_hash     TEXT,

    -- review / bookkeeping
    needs_review        INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL,

    FOREIGN KEY (geo_cell) REFERENCES place (h3_cell)
);
CREATE INDEX IF NOT EXISTS idx_media_taken_at   ON media (taken_at);
CREATE INDEX IF NOT EXISTS idx_media_local_date ON media (local_date);
CREATE INDEX IF NOT EXISTS idx_media_geo_cell   ON media (geo_cell);
CREATE INDEX IF NOT EXISTS idx_media_h3_cell    ON media (h3_cell);
CREATE INDEX IF NOT EXISTS idx_media_review     ON media (location_source, needs_review);
