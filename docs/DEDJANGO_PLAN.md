# De-Django + SQLite schema redesign — plan

Status: **for review** (not started). Supersedes the ORM assumptions in
`REARCH_PLAN.md` for the data layer.

## Goal

Remove Django entirely. The project becomes a set of plain-Python CLIs over a
local SQLite database (no ORM, no web framework, no cloud/deploy story). The
Flask timeline viewer (`viewer/`) and the `build-timeline` skill already work
this way; this makes the ingest/geocode/export pipeline consistent with them.

## Decisions locked (from discussion)

- **No ORM.** stdlib `sqlite3` + hand-written SQL behind a thin repository
  (`db.py`); rows carried as `@dataclass`.
- **SQLite-only, local.** Drop Postgres/Cloud Run. Delete Docker/deploy files.
- **No HTTP API.** Drop django-ninja. Flask viewer is the only server.
- **Fresh DB from a hand-authored `schema.sql`.** Full schema redesign.
- **No data-migration script.** Re-run `ingest` against the Takeout source to
  populate the new DB. Correctness is verified by **comparing the old Django DB
  against the new DB** (see Verification).
- **Two tables:** `media` (one row per photo) + `place` (one row per geocoded
  location cell). `media.geo_cell` → `place.h3_cell`.
- **Geocode split into fetch + derive.** Fetch (Google, costs API) stores the raw
  response; derive (offline, free, re-runnable) extracts `city`/`admin1`.
- **Per-photo blobs inline** on `media` (`sidecar_raw`, `exif`). Geo raw lives in
  `place` (shared across photos in the same cell).
- **`local_date` stored** on `media` (computed from `taken_at` + `timezone`).
- **Thumbnails default → `userdata/thumbnails`.** `data/` holds only the DB;
  `userdata/` holds all generated output (`timelines/`, `thumbnails/`), gitignored.
- **Unified CLI:** `track-me ingest | geocode | export | timeline | serve`.
- **CLI framework:** stdlib `argparse` (zero new dep). *(open — see below)*

## New schema (`track_me/schema.sql`)

```sql
-- One row per geocoded location cell. Shared by every photo whose geo_cell
-- resolves here, so the Google response is stored once, not per photo.
CREATE TABLE place (
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
CREATE INDEX idx_place_country ON place (country_code);
CREATE INDEX idx_place_city    ON place (city);

-- One row per photo/video. Scan-hot: timeline queries walk this ordered by time.
CREATE TABLE media (
    id                  INTEGER PRIMARY KEY,
    dedupe_key          TEXT    NOT NULL UNIQUE,
    google_photos_url   TEXT,

    file_name           TEXT    NOT NULL,
    kind                TEXT    NOT NULL DEFAULT 'photo'
                          CHECK (kind IN ('photo','video')),
    source_path         TEXT,               -- last-seen Takeout path (info only)

    -- time (UTC, ISO-8601 text; e.g. '2019-06-02T09:14:00+00:00')
    taken_at            TEXT,
    taken_at_source     TEXT    CHECK (taken_at_source IN
                          ('sidecar','exif','file_mtime','manual')),
    timezone            TEXT,               -- IANA zone at the location
    local_date          TEXT,               -- 'YYYY-MM-DD' in local tz (bucketing)

    -- location
    latitude            REAL,
    longitude           REAL,
    h3_cell             TEXT,               -- res 11; coarser cells derived on demand
    geo_cell            TEXT,               -- FK -> place.h3_cell (set by geocode)
    location_source     TEXT    NOT NULL DEFAULT 'none'
                          CHECK (location_source IN
                          ('exif_gps','takeout_geodata','manual','interpolated','none')),

    -- per-photo raw metadata (unique per photo -> inline)
    sidecar_raw         TEXT,               -- JSON
    exif                TEXT,               -- JSON

    -- media handling
    thumbnail_cached_at TEXT,
    perceptual_hash     TEXT,

    -- review / bookkeeping
    needs_review        INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL,

    FOREIGN KEY (geo_cell) REFERENCES place (h3_cell)
);
CREATE INDEX idx_media_taken_at   ON media (taken_at);
CREATE INDEX idx_media_local_date ON media (local_date);
CREATE INDEX idx_media_geo_cell   ON media (geo_cell);
CREATE INDEX idx_media_h3_cell    ON media (h3_cell);
CREATE INDEX idx_media_review     ON media (location_source, needs_review);
```

Notes:
- `place` is created first so the FK target exists. FK enforcement via
  `PRAGMA foreign_keys = ON`; geocode upserts the `place` row before setting
  `media.geo_cell`, so the constraint always holds.
- H3 cell IDs self-encode their resolution, so `place` keyed by `h3_cell` is
  unambiguous. Only one geocode resolution is active at a time (default res 9);
  changing it is an offline rebuild of `geo_cell` + a geocode pass for new cells.

## Geocode redesign (`places/geocode.py`)

Two steps over the H3-batched cells:

1. **Fetch** (Google, costs API — one call per new cell):
   store `place.center_lat/lng`, `country_code`, `formatted_address`,
   `geocode_raw` (full `address_components`), `geocoded_at`.
2. **Derive** (offline, free, re-runnable over stored `geocode_raw`):
   `city` = first present of
   `locality → postal_town → sublocality_level_1 → administrative_area_level_3 →
   administrative_area_level_2`; `admin1` = `administrative_area_level_1`
   (`long_name`); `country_code` = `country` (`short_name`).

Wrong city for some country? Retune the chain and re-run **derive** over the DB
— no API calls. `geocode --derive-only` re-derives; a plain `geocode` fetches new
cells then derives.

`media.geo_cell` is set during geocode: `h3.cell_to_parent(h3_cell, R)`, upsert
the `place` row, set `geo_cell`. Timeline/export read place names via
`JOIN place ON media.geo_cell = place.h3_cell`.

## New / changed files

| File | Change |
|---|---|
| `track_me/config.py` | **new** — dotenv settings: `DATA_DIR`, `DB_PATH` (`data/track_me.db`), `THUMBNAIL_CACHE_DIR` (default `userdata/thumbnails`), `THUMBNAIL_SIZE`, `PHOTOS_BASE_DIR`, `GOOGLE_MAPS_API_KEY`. No Django/cloud keys. |
| `track_me/schema.sql` | **new** — the DDL above. |
| `track_me/db.py` | **new** — connection + `PRAGMA`, `init_schema()`, `Media`/`Place` dataclasses, repository fns. |
| `track_me/cli.py` | **new** — `argparse` subcommands: `ingest`, `geocode`, `export`, `timeline`, `serve`. |
| `library/ingest/pipeline.py` | rewire upsert to `db.py`; write blobs inline; compute `local_date`; `settings.*`→`config.*`; `dj_tz.now()`→`datetime.now(UTC)`. |
| `places/geocode.py` | fetch+derive; write `place`; set `geo_cell`; priority chain; drop `Q`/`settings`/`dj_tz`. |
| `library/export.py` | `located_items` → repo query; join `place`; year filter via `local_date`. |
| `library/search.py` | swap the one `django.utils.timezone` use for stdlib; no other change. |
| `library/models.py` | **deleted** (fields → `Media` dataclass + helpers in `db.py`). |
| `.claude/skills/build-timeline/scripts/timeline_lib.py` | point SQL at `media`/`place` (join); read stored `place.city` for city level; use `local_date`. |
| `viewer/app.py` | unaffected (reads `userdata/timelines/*.json`), but bump any doc referencing old names. |
| `pyproject.toml` | add `[project.scripts] track-me`; drop Django/cloud deps. |

**Pure-Python, unchanged:** `ingest/exif.py`, `ingest/sidecar.py`, `ingest/matcher.py`,
`tz.py`, `media/thumbnails.py`.

## Deletions

- **Django framework:** `manage.py`, `track_me/settings.py`, `asgi.py`, `wsgi.py`,
  `api.py`, `urls.py`, all `migrations/`, `apps.py`, both `management/` trees,
  `library/models.py`.
- **Cloud/deploy:** `Dockerfile`, `entrypoint.sh`, `.dockerignore`, `source.bat`.
- **Tailwind:** `assets/`, `.django_tailwind_cli/`.
- **Deps dropped:** `django`, `django-ninja`, `django-tailwind-cli`, `whitenoise`,
  `gunicorn`, `dj-database-url`, `psycopg`, `pytz`; dev: `pytest-django`,
  `django-stubs`. **Kept:** `flask`, `googlemaps`, `h3`, `imagehash`, `pillow`,
  `pillow-heif`, `pydantic`, `python-dotenv`, `pyyaml`, `reverse-geocoder`,
  `timezonefinder`, `dateparser`.

## Phases (each leaves a runnable state)

1. **Foundation** ✅ — `config.py`, `schema.sql`, `db.py` (dataclasses + repo) +
   `tests/test_db.py` (9 tests, passing). Touches no existing code.
2. **Rewire pipeline** ✅ — `ingest` writes the new DB; blobs inline; `local_date`
   computed. The `track-me` CLI (`ingest` with `--force/--thumbnails/--limit`,
   plus `serve`) was pulled forward here so re-ingest never touches `manage.py`;
   `geocode`/`export`/`timeline` subcommands land as they're rewired. Re-ingest
   the Takeout source into a fresh `data/track_me.db` (old Django DB preserved as
   `data/track_me_legacy.db` for comparison).
3. **Rewire geocode + export** — fetch/derive + `place` + `geo_cell`; export join.
   Run `geocode` (small `--max-api-calls` first) to populate `place`.
4. **CLI + consumers** — single `track-me` entrypoint with subcommands
   `ingest | geocode | export | timeline | serve`, where **`serve` launches the
   Flask viewer** (the viewer folds into the main CLI, not a separate command).
   Retire the management commands; update `timeline_lib.py` + viewer to
   `media`/`place`. **Document every `track-me` subcommand in `CLAUDE.md`**
   (replacing the old `python manage.py …` sections).
5. **Tests + teardown** — temp-sqlite `conftest` fixture; rewrite the 3 `django_db`
   test files (`test_ingest_pipeline`, `test_geocode`, `test_export`); delete
   Django/Docker/Tailwind files; drop deps; final `ruff` + `ty` + `pytest`.

## Verification — old vs new (the correctness gate)

`dedupe_key` logic is unchanged, so rows in the legacy Django DB and the freshly
re-ingested new DB align 1:1 on `dedupe_key`. A comparison script (`ATTACH` both
DBs) checks the **ingest-produced** fields:

- **Counts:** total rows; located (`latitude IS NOT NULL`); per `kind`;
  with-sidecar; `needs_review`.
- **Per `dedupe_key`:** `taken_at` (same UTC instant), `latitude`/`longitude`
  (within float rounding), `timezone`, `location_source`, `taken_at_source`,
  `file_name`, `google_photos_url`.
- **New fields:** `local_date` sanity-checked against `taken_at` + `timezone`
  (esp. photos near midnight in non-UTC zones).
- **Geocode fields excluded** (new DB is geocoded separately). Optionally, after
  re-geocoding, compare `place.country_code` / `formatted_address` for cells that
  map to legacy `country_code` / `place_label`.

Report: keys only-in-old / only-in-new (should be empty) and per-field mismatch
counts. Any mismatch is a rewrite bug to fix before teardown.

## Decisions (resolved in review)

1. **Geo table name:** `place`.
2. **CLI framework:** stdlib `argparse` (no new dep).
3. **`--resolution` knob:** kept configurable, default res 9; `estimate` retained
   for cost preview. Changing resolution is an offline `geo_cell` rebuild + a
   geocode pass for new cells.
4. **City at geocode "derive" stage.** `place.city`/`admin1` are computed offline
   from stored `geocode_raw` (not at query time). Retuning the priority chain and
   running `geocode --derive-only` recomputes them over the DB with **no API
   calls**. The `build-timeline` skill's city level reads `place.city` via the
   join instead of on-the-fly `reverse_geocoder` — which then becomes removable
   (metro proximity-clustering stays; it's independent of naming).
5. **Legacy DB** preserved as `data/track_me_legacy.db` for the old-vs-new
   comparison.
```
