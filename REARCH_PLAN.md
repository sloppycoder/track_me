# Re-architecture Plan (clean rebuild)

Google Takeout is the source of truth. Parse the sidecar JSON for the
authoritative timestamp, Google's location, and the deep link back to Google
Photos. Keep geotagging + thumbnails, add a clean spot-check UI, support a
repeatable incremental Takeout workflow where the extracted folder is transient.

The old `myphoto` app and its photo-centric model are treated as **baggage and
replaced**. All data is re-derivable from Takeout, so we reset the schema.

## Stack decisions (locked)

- **Fresh schema / DB reset.** Drop old migrations + unused fields; re-ingest.
- **django-ninja + Pydantic.** One type system end to end: the same Pydantic
  models parse the sidecar JSON *and* serialize the API.
- **HTMX + Alpine.js** for the UI (server-rendered partials), keeping Tailwind +
  DaisyUI. Replaces the 712-line vanilla-JS template.

---

## Target structure

Domain-oriented Django apps, thin views, services do the work. (Apps can be
collapsed if you'd rather have fewer — but the boundaries below are the clean cut.)

```
track_me/
  settings.py            # trimmed; env via django-environ
  api.py                 # NinjaAPI() root, mounts routers, /api/*
  urls.py                # web pages + api.urls

library/                 # the catalog: media items + ingestion
  models.py              # MediaItem (single clean model)
  ingest/
    sidecar.py           # Pydantic Sidecar schema + JSON parse
    matcher.py           # image <-> sidecar filename cascade
    exif.py              # EXIF fallback extraction
    pipeline.py          # discover -> merge -> dedupe -> upsert -> thumbnail
  media/
    thumbnails.py        # eager, content-addressed thumbnail cache
  api.py                 # ninja router: list/search/locate
  management/commands/ingest.py

places/                  # location naming
  geocode.py             # H3-batched reverse geocode (kept, refactored)
  h3utils.py             # one stored cell -> derive parents
  management/commands/geocode.py

timeline/                # Phase 4 (later)
  models.py              # Stay, Trip
  engine.py              # deterministic interpolation + segmentation
  api.py                 # footprints/timeline router

web/                     # HTMX UI
  views.py               # render pages + HTML partials
  templates/             # base, spotcheck grid, modal partial, footprints
  static/                # alpine init, minimal JS
```

---

## Clean data model (`library/models.py`)

One model, lean. Re-derivable, content-addressed.

```python
class MediaItem(models.Model):
    # identity (stable across Takeout dumps)
    dedupe_key = models.CharField(max_length=64, unique=True, db_index=True)
    google_photos_url = models.URLField(max_length=512, null=True, blank=True)

    # file / kind
    file_name = models.CharField(max_length=255)
    kind = models.CharField(max_length=10, default="photo")   # photo | video
    last_source_path = models.CharField(max_length=512, blank=True)  # info only

    # time (authoritative, tz-aware) -- set for EVERY item at ingest
    taken_at = models.DateTimeField(null=True, db_index=True)
    time_source = models.CharField(max_length=12, null=True)  # sidecar|exif|file_mtime|manual
    timezone = models.CharField(max_length=64, null=True)     # IANA zone at the location (offline-derived)

    # location
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True)
    h3_cell = models.CharField(max_length=16, null=True, db_index=True)  # ONE fine res (11)
    location_source = models.CharField(max_length=14, null=True)
        # exif_gps | takeout_geodata | manual | interpolated | none

    # geocoded names
    place_label = models.CharField(max_length=255, blank=True)
    country_code = models.CharField(max_length=2, null=True, db_index=True)
    geocoded_at = models.DateTimeField(null=True)

    # media handling
    thumbnail_cached_at = models.DateTimeField(null=True)
    perceptual_hash = models.CharField(max_length=16, null=True, db_index=True)

    # review + provenance
    needs_review = models.BooleanField(default=False, db_index=True)
    sidecar_raw = models.JSONField(null=True)   # parsed sidecar kept for provenance
    exif = models.JSONField(null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

**Deliberate simplifications vs the old `Photo`:**
- **One `h3_cell`** (res 11) instead of five columns. Coarser cells are derived on
  demand with `h3.cell_to_parent(cell, res)` — footprints/geocoding pick a target
  resolution at query time. Removes 4 columns and the model's `calculate_h3_indexes`.
- **One `taken_at`** instead of `date_time_original_text` + `date_time_taken`
  (raw EXIF datetime still lives in `exif` JSON if ever needed).
- **One `perceptual_hash`** (drop average/difference hashes — unused).
- **Dropped** `cluster_latitude/longitude` (legacy, unused).
- **`dedupe_key`** = Google Photos URL slug if present, else
  `sha1(title + photoTakenTime)`, else perceptual hash. Survives DB resets and
  cross-dump overlaps; replaces `source_file` as identity.

---

## Phase 0 — Scaffold the clean foundation

- Add deps: `django-ninja`, `pydantic`, `django-environ` (uv). HTMX + Alpine via
  static assets (CDN or vendored).
- Create `library`, `places`, `web` apps (timeline later). Wire `track_me/api.py`
  (`NinjaAPI`) and mount routers; `urls.py` serves web pages + `api.urls`.
- New `MediaItem` model + **fresh initial migration**. Delete old `myphoto` app
  and its migrations.
- Trim `settings.py` (env-based config; keep Tailwind/DaisyUI, thumbnail/photo
  dirs, footprint step bounds).

**Acceptance:** server boots, `/api/docs` (ninja's auto OpenAPI) renders, empty DB.

---

## Phase 1 — Ingestion (the core; highest value)

**Outcome:** point at a Takeout extract → every item gets a timestamp, most get a
location + Google Photos link, thumbnails cached eagerly.

### `library/ingest/sidecar.py` (Pydantic)
```python
class GeoData(BaseModel):
    latitude: float = 0.0
    longitude: float = 0.0
    def coords(self) -> tuple[float, float] | None:
        # exact (0.0, 0.0) means "no location", not Gulf of Guinea
        return None if (self.latitude == 0 and self.longitude == 0) else (self.latitude, self.longitude)

class Sidecar(BaseModel):
    title: str | None = None
    photoTakenTime: dict | None = None        # {"timestamp": "1564920000"}
    geoData: GeoData | None = None
    geoDataExif: GeoData | None = None
    url: str | None = None                     # deep link to photos.google.com
```

### `library/ingest/matcher.py` — sidecar filename cascade
Takeout naming is messy; index all `.json` up front, then resolve per image:
1. `IMG_1234.JPG.json` (classic)
2. `IMG_1234.JPG.supplemental-metadata.json` (2023+; and truncated `...supplemental-met.json`)
3. length-truncated base names (Takeout caps total filename length) → longest-prefix match in same dir
4. duplicate counter moves: `IMG_1234(1).JPG` → `IMG_1234.JPG(1).json`
5. edited photos (`IMG_1234-edited.JPG`) reuse the base sidecar
6. fallback: match sidecar `title` field to image name within the album dir

### `library/ingest/pipeline.py` — orchestration
Per item, merge sources with a clear precedence:
- **time:** sidecar `photoTakenTime` (epoch UTC) → EXIF `DateTimeOriginal` → file
  mtime; record `time_source`. Timestamp set for **every** item (decoupled from
  geocoding — the old design's core flaw).
- **location:** EXIF GPS (`exif_gps`) → sidecar `geoData` (`takeout_geodata`) →
  none (`needs_review=True`). Compute `h3_cell` whenever coords exist.
- **link:** store `google_photos_url`.
- **identity:** compute `dedupe_key`; upsert (refresh metadata, never clobber a
  `manual` location on re-ingest).
- **thumbnail:** generate + cache **eagerly** now (see below).

### `library/media/thumbnails.py` — content-addressed
- Thumbnail filename keyed by `dedupe_key` (not DB id), so it survives DB resets.
- **Opt-in** at ingest via `--thumbnails` (default off): the timeline data doesn't
  need them, so they're only generated when a UI will display them. When generated,
  `thumbnail_cached_at` is set; serving never depends on the original being present
  (missing original + cached thumb = fine, original viewed via Google link).

### `library/management/commands/ingest.py`
Replaces `process_photos`. Re-runnable; dedupes across dumps.

### Tests
- `tests/fixtures/` tree reproducing the matcher quirks (supplemental-metadata,
  truncation, `(1)` counters, edited, missing sidecar).
- No-EXIF/no-GPS item with sidecar still gets `taken_at` + `google_photos_url`.

**Acceptance:** ingest one real Takeout; in shell, `taken_at` non-null ≈ total,
high `google_photos_url` coverage; delete the extract, thumbnails still load.

---

## Phase 2 — Location naming (`places`)

- Port the H3-batched reverse geocoder (it's the one genuinely good piece of the
  old code) to the new model. Batch by a **coarse parent** of `h3_cell`
  (`cell_to_parent(cell, 9 or 10)`) to keep the API-call savings.
- `geocode` command + reusable service; sets `place_label`, `country_code`,
  `geocoded_at`. `taken_at` (an absolute UTC instant from the sidecar) is left
  untouched. Note: a separate **offline** step derives each item's IANA `timezone`
  from its coordinates (no API), used for local-time bucketing in the timeline.
  `geocode --estimate` counts API calls/cost without spending any.

---

## Phase 3 — Spot-check UI (HTMX + Alpine) + API

**Outcome:** eyeball items + inferred location, one click to the Google Photos
original, manually fix the long tail.

- **django-ninja API** (`library/api.py`): `GET /api/media` (smart search +
  filters incl. `no_location`, `needs_review`), `POST /api/media/locate` (manual
  geotag, `bulk_update`), thumbnail endpoint serving the cache.
- **HTMX UI** (`web/`): search box → HTML grid partial; click → modal partial
  with map, `location_source` badge, and **"View on Google Photos ↗"**
  (`google_photos_url`). Alpine handles multi-select + map pin state.
- Manual geotag sets `location_source="manual"` and recomputes `h3_cell`.

---

## Phase 4 — Timeline reconstruction (later, the payoff)

Cheap once Phases 0–3 land and every item has a timestamp:
- `timeline` app: `Stay` (place, start/end, centroid/cell, count) and `Trip`
  (ordered stays + name + narrative).
- Deterministic `engine.py`: order by `taken_at`; located items are anchors;
  **interpolate** gaps from nearest anchors (`location_source="interpolated"`);
  collapse runs into Stays. Rebuild Footprints to read stored Stays.
- AI agent layer: name trips + write narrative over `Stay`/`Trip` rows (not raw
  photos). This is the right place to "leave it to an AI agent."

---

## Order & effort

| Phase | Effort | Notes |
|-------|--------|-------|
| 0 — scaffold (apps, ninja, model, fresh migration) | ~0.5 day | Enables the rest |
| 1 — ingestion (sidecar + thumbnails + dedupe) | ~1–1.5 day | **Highest value** |
| 2 — geocoding port | ~0.5 day | Reuses old logic |
| 3 — HTMX/Alpine spot-check UI + ninja API | ~1 day | Daily driver |
| 4 — timeline engine + agent | later | The end goal |

Recommendation unchanged: build Phase 1 first and run it on one real Takeout to
measure the location/timestamp hit rate before going further. If you drop one
anonymized sidecar `.json` into `tests/fixtures/`, I'll match the parser to your
export's exact format.

---

## Day-to-day usage (after the refactor)

### One-time setup
```bash
uv sync                                 # install deps (django-ninja, pydantic, ...)
echo "GOOGLE_MAPS_API_KEY=..." > .env   # only required env var; db + thumbnails live under data/
python manage.py migrate                # create the fresh schema (data/track_me.db)
```

### Each incremental Takeout (the repeatable loop)
```bash
# 1. unzip the new Takeout somewhere temporary
unzip ~/Downloads/takeout-2026-06.zip -d /tmp/takeout-2026-06

# 2. INGEST: parse sidecars + EXIF, set timestamps, locations, Google links,
#    compute dedupe keys (thumbnails opt-in via --thumbnails)
python manage.py ingest /tmp/takeout-2026-06

# 3. GEOCODE: turn coordinates into place names + country (H3-batched, cheap)
python manage.py geocode --resolution 9

# 4. (optional) export located media as a GPX/GeoJSON track for timeline tools
python manage.py export_gpx --format gpx --output track.gpx

# 5. (Phase 4, later) build the timeline from located items
python manage.py build_timeline

# 6. delete the extract — thumbnails + Google links persist, originals via the link
rm -rf /tmp/takeout-2026-06
```
`ingest` and `geocode` are **re-runnable and incremental**: already-seen items
(matched by `dedupe_key`) are skipped, and manual geotags are never overwritten.

### Start the UI
```bash
python manage.py tailwind build         # compile CSS first (required)
python manage.py tailwind runserver     # serves UI + API with CSS watch
```
Then open:
- **Spot-check / geotag UI:** http://localhost:8000/
- **Footprints timeline:** http://localhost:8000/footprints/   (Phase 4)
- **API docs (auto-generated by ninja):** http://localhost:8000/api/docs

### Spot-checking the long tail
In the UI search box, filter `no_location` or `needs_review` to find items with no
location, set a pin on the map to geotag them, and click **"View on Google
Photos ↗"** in the modal to confirm against the original.

### Tests / quality
```bash
pytest
ruff check . --fix && ruff format .
ty check .
```
