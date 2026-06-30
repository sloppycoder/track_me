# Instructions for coding agents

This project is being re-architected. See **docs/REARCH_PLAN.md** (overall plan,
Phases 0–4) and **docs/PHASE_3_UI_PLAN.md** (the UI, on hold). The code lives in
the `library` and `places` apps. The legacy `myphoto` app has been **deleted**;
its one reusable piece (smart-search parsing) was salvaged into `library/search.py`.
There is no web UI yet — `/` redirects to the API docs.

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

**Only run ruff/ty on Python (.py) files. DO NOT run them on HTML, CSS, JS, or
templates.**

After any code change, run, in order:
1. `ruff format <changed_file.py>`
2. `ruff check . --fix`
3. `ruff check .`
4. `pytest`
5. `ty check .`

Project rules: line length **98**, indent **4 spaces**, imports at top of file,
PEP8. Pre-commit runs ruff + ty (see `.pre-commit-config.yaml`).

## Database

Local-first **SQLite** at `data/track_me.db` (the `data/` dir holds the db and
thumbnails; auto-created at startup, gitignored). Set `DATABASE_URL` to override
with Postgres (e.g. Cloud Run). The fresh schema is re-derivable — recreate with
`python manage.py migrate`.

## Ingestion & geocoding pipeline (new)

```bash
# 1. Ingest a Google Takeout extract: parse sidecar JSON + EXIF, set taken_at for
#    every item, resolve location, store the Google Photos URL (thumbnails opt-in).
python manage.py ingest <takeout-dir>        # directory arg is REQUIRED

# 2. Reverse-geocode located items into place names (H3-batched, Google API).
python manage.py geocode [--resolution 9] [--max-api-calls N] [--recalculate]
```

Both commands are re-runnable/incremental (dedupe by `MediaItem.dedupe_key`) and
never overwrite manual edits. Key model: `library/models.py::MediaItem`
(`taken_at`, `latitude/longitude`, single `h3_cell`, `location_source`,
`time_source`, `google_photos_url`, `needs_review`, content-addressed thumbnails).

## Answering travel/trip questions ("which countries/cities did I visit, with dates")

When asked to turn the photo catalog into a **timeline of distinct trips/visits**,
apply this recipe (validated against `data/track_me.db`):

1. **Pull located photos in time order.** Filter to the requested window, require
   coordinates, `order_by("taken_at")`. Use `taken_at` (UTC) for ordering; note in
   the answer that day boundaries are UTC (offer `local_taken_at` if it matters).
2. **Pick the place label per photo:**
   - *Country-level* — use `country_code` directly.
   - *City-level* — do NOT parse `place_label` (it's a full formatted address; the
     city sits at a different comma-index per country). Reverse-geocode the
     coordinates **offline** with the `reverse_geocoder` package (a project
     dependency; GeoNames nearest-city, no API key/network). Use `name, admin1 (cc)`
     as the key. NOTE: it is installed but NOT wired into the model/pipeline yet —
     it's an analysis aid only; there is no stored `city` column, so derive at query
     time. (`MediaItem` stores only free-text `place_label` + `country_code`.)
3. **Segment into contiguous runs:** walk the ordered list, start a new trip each
   time the label changes; track `from`/`to` = first/last `taken_at` of the run.
4. **Smooth border/blip noise:** absorb any run lasting **< 24h that is bracketed
   by the same label on both sides** (same-day border crossings, single stray
   photos), then re-coalesce adjacent same-label runs. Repeat to a fixed point.
5. **City-level only — collapse metros by proximity:** raw GeoNames names over-split
   a metro into neighborhoods (Madrid→Retiro/Salamanca; Athens suburbs). Cluster
   consecutive photos within **~50 km** (haversine vs running centroid) into one
   stay, label it by the *most common* city in the cluster. The radius is a knob —
   bigger merges adjacent cities, smaller splits metros into districts.

Output: chronological, **non-overlapping** date ranges; list a place once per
distinct visit (a revisited city appears multiple times). Ignore photo counts
unless asked. Day-trips from a base city legitimately appear as their own stays.
Reusable scripts live in the session scratchpad (`city_trips*.py`).

## API

django-ninja, mounted at `/api` (auto docs at `/api/docs`); root in
`track_me/api.py`. App routers are added as features land.

## Development Server

Always compile Tailwind CSS before serving (UI is broken without it):

```bash
python manage.py tailwind build       # compile once
python manage.py tailwind runserver   # compile + watch + runserver
```

## Testing

```bash
pytest                       # full suite (fast; SQLite in-memory)
pytest tests/test_xxx.py -v  # one file
pytest --cov                 # coverage
```

- Test settings: `tests/settings.py` (SQLite in-memory; WhiteNoise disabled).
- Real-world sidecar fixtures live in `tests/fixtures/` (anonymized) — add new
  Takeout quirks there as regression cases.
- **Playwright UI tests** are gated to macOS: mark them `@pytest.mark.playwright`
  and a `conftest.py` hook auto-skips them off macOS (CI / remote Linux). Install
  browsers with `playwright install chromium`. (No UI tests exist yet — Phase 3.)

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
