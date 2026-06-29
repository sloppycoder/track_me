# Phase 3 — Spot-check UI (HTMX + Alpine)

> **Status: ON HOLD — may not be needed.** Phases 1–2 are validated and the catalog
> answers real travel questions directly from the DB (country/city trips, GPX
> export) with no UI at all, so a browse/visualization UI is a nice-to-have, not
> load-bearing. The one piece with a genuine unmet need is **manually geotagging the
> long tail** (pre-GPS photos with no location + `needs_review` items) — and even
> that could be a small CSV-in/CSV-out helper rather than the full HTMX/Alpine/map
> app described below. Revisit before building; don't implement on spec.

The plan below is preserved as the original design, to pick up *if* a UI is wanted.
It assumes Phases 1 (ingest) and 2 (geocoding) are validated on the full dataset.

Goal: a lightweight UI to eyeball ingested media, see where each item's
location/time came from, jump to the original on Google Photos, and manually fix
the long tail (especially old pre-GPS photos). Built on the new stack
(django-ninja + HTMX + Alpine + Tailwind/DaisyUI), replacing the legacy
`myphoto` UI, which is then removed.

## Prerequisites
- Phase 1 + 2 landed: `MediaItem` rows have `taken_at`, `location_source`,
  `google_photos_url`, cached thumbnails, and (where possible) `place_label` /
  `country_code`.
- The legacy `myphoto` app has **already been removed** (app, templates, urls,
  legacy tests, ty exclude). Its smart-search parser was salvaged into
  `library/search.py` (tests in `tests/test_search.py`) — wire the new search on
  top of that rather than re-porting.

## Scope

### 1. API (django-ninja) — `library/api.py`, mounted at `/api/media`
- `GET /api/media` — paginated list/search. Query params:
  - `q`: smart search (use `library.search.parse_smart_search` — already salvaged).
  - filters: `location_source` (e.g. `none`), `needs_review`, `country`,
    `year`/date-range, `has_url`.
  - returns Pydantic `MediaOut` (id, file_name, taken_at, lat/lon,
    location_source, time_source, place_label, country_code, needs_review,
    thumbnail_url, google_photos_url).
- `GET /api/media/{id}/thumbnail` — serve the content-addressed cached thumbnail
  (by `dedupe_key`); 404 if absent. No on-the-fly generation (originals are
  transient).
- `POST /api/media/locate` — manual geotag: body `{ids: [...], lat, lon}`.
  Sets coords via `MediaItem.set_location(..., source="manual")`, recomputes
  `h3_cell`, clears `needs_review`, `bulk_update`. Optionally reverse-geocode
  immediately via the Phase 2 `Geocoder.reverse_geocode` to fill
  `place_label`/`country_code`.
- `POST /api/media/{id}/review` — toggle/clear `needs_review`.

### 2. UI (HTMX + Alpine) — `web/` app
Server-rendered partials, no SPA/build step:
- **Base layout** (`web/templates/web/base.html`): Tailwind + DaisyUI, HTMX +
  Alpine via CDN or vendored static.
- **Spot-check grid** (`/`): search box (`hx-get` → grid partial), responsive
  thumbnail grid. Each card shows a `location_source` badge
  (EXIF / Google / Manual / None) and a `needs_review` flag.
- **Detail modal** (HTMX partial on card click): larger thumbnail, metadata,
  a map (Google Maps JS — port the integration from the legacy templates),
  **"View on Google Photos ↗"** bound to `google_photos_url`, and a manual
  geotag control (drop a pin → `POST /api/media/locate`).
- **Long-tail workflow**: a one-click filter for `no_location` / `needs_review`
  so the user can sweep un-located photos (the bulk of pre-smartphone years)
  and geotag them, individually or multi-select.
- Alpine handles client state: multi-select set, map pin, modal open/close.

### 3. Map + reverse geocode
- Reuse the Google Maps JS (key from `settings.GOOGLE_MAPS_API_KEY`).
- On manual pin placement, call `/api/media/locate`; show the returned
  `place_label`/`country_code` as confirmation.

### 4. Tests
- ninja API tests (search filters, locate, thumbnail serving) — plain pytest.
- Playwright UI tests marked `@pytest.mark.playwright` so they only run on macOS
  (gate already in `conftest.py`): grid renders, search filters, modal opens,
  Google Photos link present, manual geotag round-trips and flips the badge to
  "Manual".

## Cleanup when this ships
- ~~Delete the `myphoto` app and legacy tests~~ — **done** (app removed,
  `parse_smart_search` salvaged to `library/search.py`, root `/` now redirects to
  `/api/docs`).
- Replace that root redirect with the real spot-check grid view.
- Revisit the `_build_tailwind_css` session fixture: keep it for the new UI tests,
  or drop it if no template tests end up existing.

## Out of scope (later phases)
- Timeline reconstruction (`Stay`/`Trip`, interpolation) — Phase 4.
- AI agent trip naming/narrative — Phase 4+.
- Full byte-level dump / self-hosted originals.

## Open questions to settle before building
- Pagination/scale: how many items will the grid realistically page through?
  (Drives whether plain offset pagination is fine or we need keyset.)
- Map provider: stay on Google Maps JS, or switch to a free tile provider
  (Leaflet + OSM) to avoid the Maps JS billing entirely for a personal tool?
- Thumbnail size/quality for the grid vs modal.
