# Instructions for coding agents

Local-first tool that turns Google Takeout photo exports into a queryable travel
timeline, then visualizes it on Google Maps. **No Django, no ORM, no web
framework beyond a tiny Flask viewer** — plain Python over local SQLite.

All code is one package under `src/` (src-layout):

```
src/track_me/
  cli.py         # the `track-me` entrypoint (argparse subcommands)
  config.py      # dotenv-backed settings; all state under userdata/
  db.py          # SQLite data layer: Media/Place dataclasses + Database repository
  schema.sql     # hand-authored schema (media + place)
  ingest/        # exif, sidecar, matcher, pipeline
  geocode.py     # reverse-geocoding (fetch + derive)
  timeline.py    # build travel timelines from the catalog
  export.py      # GPX / GeoJSON export
  thumbnails.py  # opt-in thumbnail cache
  tz.py          # offline timezone lookup
  search.py      # smart-search query parsing (salvaged; not yet wired in)
  viewer/        # Flask app + templates (renders timelines on Google Maps)
```

## Key Dependencies
- Python 3.12+
- UV package manager (`uv.lock` present) — use `uv sync`, `uv add`, `uv run`.

## Code Quality

```bash
ruff check .            # lint
ruff check . --fix      # lint + autofix
ruff format <file.py>   # format a file (recommended after editing)
ty check .              # type check (ty is the type checker; pyright is NOT used)
```

**Only run ruff/ty on Python (.py) files. DO NOT run them on HTML, CSS, or JS
(e.g. the viewer templates).**

After any code change, run, in order:
1. `ruff format <changed_file.py>`
2. `ruff check . --fix`
3. `ruff check .`
4. `pytest`
5. `ty check .`

Project rules: line length **98**, indent **4 spaces**, imports at top of file,
PEP8. Pre-commit runs ruff + ty (see `.pre-commit-config.yaml`).

## Database

Local **SQLite** at `userdata/track_me.db` (override with `DB_PATH`). The schema is
hand-authored in `src/track_me/schema.sql` and created on first use
(`Database(...).init_schema()` runs `CREATE TABLE IF NOT EXISTS`) — no migrations.

Two tables (`src/track_me/db.py`):
- **`media`** — one row per photo/video (`Media` dataclass). `taken_at` (ISO-8601
  UTC text), `local_date` (local-day bucket), `latitude`/`longitude`, `h3_cell`
  (res 11), `geo_cell` (FK → `place.h3_cell`, set at geocode time),
  `location_source`, `taken_at_source`, `timezone`, `google_photos_url`,
  `needs_review`, and inline `sidecar_raw`/`exif` JSON.
- **`place`** — one row per geocoded H3 cell (`Place` dataclass), shared by every
  photo in the cell: `city`, `admin1`, `country_code`, `formatted_address`,
  `geocode_raw` (Google `address_components`), `geocoded_at`.

All access goes through the `Database` repository (raw SQL, no ORM). Everything
local — the DB, `thumbnails/`, `timelines/` — lives under `userdata/` (gitignored).

## CLI: `track-me`

Single entrypoint (installed console script; also `uv run track-me ...`). Source
in `src/track_me/cli.py`.

```bash
# 1. Ingest a Google Takeout extract (parse sidecar JSON + EXIF, set taken_at +
#    timezone + local_date, resolve location, store the Google Photos URL).
track-me ingest <takeout-dir> [--force] [--thumbnails] [--limit N]

# 2. Reverse-geocode located items (H3-batched Google calls) into place names.
#    Splits into fetch (Google, costs API) + derive (offline city/admin1).
track-me geocode [--resolution 9] [--recalculate] [--max-api-calls N] \
                 [--estimate] [--derive-only]

# 3. Export located media as timestamped GPX/GeoJSON points.
track-me export [--format gpx|geojson] [--output FILE] [--year Y]

# 4. Build a travel timeline (preview; --write persists to userdata/timelines/).
track-me timeline --start ISO --end ISO [--level country|city] [--region CC ...] \
                  [--write --id ID --title TITLE --prompt "..."]

# 5. Launch the Google Maps timeline viewer (Flask) at http://localhost:5000.
track-me serve [--port 5000]
```

Ingest and geocode are re-runnable/incremental (dedupe by `Media.dedupe_key`;
geocode skips cells already fetched) and never overwrite manual edits.

**Geocode = fetch + derive.** Fetch stores the raw Google response in
`place.geocode_raw`; derive picks `city`/`admin1` from it via a priority fallback
chain (`locality → postal_town → sublocality_1 → admin3 → admin2`), because no
single Google field is "the city" worldwide. Retune the chain and re-run
`track-me geocode --derive-only` to recompute names offline — **no API calls**.

## Building travel timelines

Prefer the **`build-timeline` skill** (`.claude/skills/build-timeline/`) — it
drives `track-me timeline` conversationally and only writes the JSON after the
user confirms. Logic lives in `src/track_me/timeline.py`. The recipe:

1. Pull located photos for the window, ordered by `taken_at` (UTC); bucket dates
   by the photo's **local day** (`local_date`).
2. Label each photo: country-level uses `place.country_code`; city-level uses the
   stored **`place.city`** (Google-derived — no query-time reverse-geocoding).
3. Segment into contiguous runs; start a new stay when the label changes.
4. Smooth border/blip noise: absorb any run < 24h bracketed by the same label,
   then re-coalesce; repeat to a fixed point.
5. City-level only: cluster consecutive photos within ~50 km (`--merge-km`) of a
   running centroid into one stay, labelled by the most common `place.city`.

Output: chronological, **non-overlapping** date ranges (a revisited place appears
once per distinct visit) → `userdata/timelines/<id>.json`, rendered by the viewer.

## Viewer

`src/track_me/viewer/app.py` (Flask) lists `userdata/timelines/*.json` and renders one on the
Google Maps JS API, injecting `GOOGLE_MAPS_API_KEY` (from `.env`) server-side. It
reads only the JSON files — never the DB. Launch with `track-me serve`.

## Testing

```bash
pytest                       # full suite (fast; temp SQLite files)
pytest tests/test_db.py -v   # one file
pytest --cov                 # coverage
```

Tests build a throwaway `Database(tmp_path / "t.db")` and exercise the repository
directly — no shared DB, no Django, no fixtures beyond a per-test temp DB. Google
geocoding is mocked (`tests/test_geocode.py`). Real-world sidecar fixtures live in
`tests/fixtures/` (anonymized) — add new Takeout quirks there as regression cases.

## Git Commit Guidelines

Always include a summary in the commit message:

```bash
git commit -m "$(cat <<'EOF'
Brief description of changes

Summary of what changed:
- specific change 1
- specific change 2
EOF
)"
```

Explain what changed and why (if not obvious).
