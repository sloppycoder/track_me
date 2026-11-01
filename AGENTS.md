# Instructions for coding agents

Local-first tool that turns Google Takeout photo exports into a queryable travel
timeline and serves it to chat clients over MCP. **No Django, no ORM, no web
framework** — plain Python over local SQLite.

All code is one package under `src/` (src-layout):

```
src/track_me/
  cli.py         # the `track-me` entrypoint (argparse subcommands)
  config.py      # dotenv-backed settings; all state under userdata/
  db.py          # SQLite data layer: Media/Place dataclasses + Database repository
  schema.sql     # hand-authored schema (media + place)
  storage.py     # object-store abstraction: LocalStore / S3Store (+ from_uri)
  ingest/        # exif, sidecar, matcher, pipeline (parallel, over the store)
  geocode.py     # reverse-geocoding (fetch + derive)
  timeline.py    # build travel timelines from the catalog
  export.py      # GPX / GeoJSON export
  thumbnails.py  # opt-in thumbnail cache
  tz.py          # offline timezone lookup
  mcp_server.py  # MCP server: read-only catalog tools for chat clients (Google OAuth)
```

## Key Dependencies
- Python 3.14+
- UV package manager (`uv.lock` present) — use `uv sync`, `uv add`, `uv run`.

## Code Quality

```bash
ruff check .            # lint
ruff check . --fix      # lint + autofix
ruff format <file.py>   # format a file (recommended after editing)
ty check .              # type check (ty is the type checker; pyright is NOT used)
```

**Only run ruff/ty on Python (.py) files. DO NOT run them on HTML, CSS, or JS
(e.g. HTML, CSS, or JS files).**

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

Three tables (`src/track_me/db.py`); `home` is described after the first two:
- **`media`** — one row per photo/video (`Media` dataclass). `taken_at` (ISO-8601
  UTC text), `local_date` (local-day bucket), `latitude`/`longitude`, `h3_cell`
  (res 11), `geo_cell` (FK → `place.h3_cell`, set at geocode time),
  `location_source`, `taken_at_source`, `timezone`, `google_photos_url`,
  `needs_review`, and inline `sidecar_raw`/`exif` JSON.
- **`place`** — one row per geocoded H3 cell (`Place` dataclass), shared by every
  photo in the cell: `city`, `admin1`, `country_code`, `formatted_address`,
  `geocode_raw` (Google `address_components`), `geocoded_at`.

- **`home`** — dated home locations (`Home` dataclass), hard-coded in
  `db.HOME_SEED` and rewritten into the table by `init_schema()`. Inclusive
  `valid_from`/`valid_to`; a gap between rows (the 2024-04-05..2026-11-25 "roaming"
  period) means no home. `Database.homes()` falls back to `HOME_SEED` if the table is
  missing (old read-only catalogs).

All access goes through the `Database` repository (raw SQL, no ORM). Everything
local — the DB and `thumbnails/` — lives under `userdata/` (gitignored).

## CLI: `track-me`

Single entrypoint (installed console script; also `uv run track-me ...`), source in
`src/track_me/cli.py`. Five subcommands: `ingest · geocode · export · timeline ·
mcp`. **The full command + flag reference lives in `README.md`** — don't
duplicate it here (it drifts). Below is the behavior agents should know.

Ingest and geocode are re-runnable/incremental (dedupe by `Media.dedupe_key`;
geocode skips cells already fetched) and never overwrite manual edits.

**Ingest (`ingest/pipeline.py`) is object-store based, parallel, and lazy.** It
reads a Takeout source through `storage.py` (`LocalStore` or `S3Store`, chosen by
`from_uri`), lists all objects once (grouped by directory in memory), and runs
per-object work in a thread pool (`--workers`, default 32) while the main thread
is the sole SQLite writer. It is **sidecar-first**: identity/time/location come
from the sidecar JSON, and the image is downloaded + decoded **only** when the
sidecar lacks location, time, or identity (the perceptual hash — a full decode —
runs only when needed for identity). `--filter` limits to a capture-month range
for quick test runs. **Change detection:** a `sidecar_fingerprint`
(coords/title/description) makes re-tagged photos `refreshed` rather than skipped,
and a moved location resets `media.geo_cell` so incremental `geocode` re-links it.

**Geocode = fetch + derive.** Fetch stores the raw Google response in
`place.geocode_raw`; derive picks `city`/`admin1` from it via a priority fallback
chain (`locality → postal_town → sublocality_1 → admin3 → admin2`), because no
single Google field is "the city" worldwide. Retune the chain and re-run
`track-me geocode --derive-only` to recompute names offline — **no API calls**.

## Building travel timelines

The **`track-me timeline` CLI** (preview only — see `README.md` for flags) and the
MCP `get_trips` tool share one deterministic engine (`src/track_me/timeline.py`,
`build_stays`). The recipe:

1. Pull located photos for the window, ordered by `taken_at` (UTC); bucket dates
   by the photo's **local day** (`local_date`).
2. Label each photo: country-level uses `place.country_code`; city-level uses the
   stored **`place.city`** (Google-derived — no query-time reverse-geocoding).
3. Segment into contiguous runs; start a new stay when the label changes.
4. Smooth border/blip noise: absorb any run < 24h bracketed by the same label,
   then re-coalesce; repeat to a fixed point.
5. City-level only: cluster consecutive photos within ~50 km (`--merge-km`) of a
   running centroid into one stay, labelled by the most common `place.city`.

6. Tag each stay `is_travel`: false when its centroid is within the home's
   `radius_km` (100 mi) of the home active on the stay's first day; always true with
   no active home. Computed at query time (never stored on `media`), so fixing a home
   date needs no backfill. `--travel-only` / MCP `travel_only` hide non-travel stays.

Output: chronological, **non-overlapping** date ranges (a revisited place appears
once per distinct visit).

## MCP server (what Cloud Run runs)

`src/track_me/mcp_server.py` (FastMCP) exposes **read-only** tools over the
catalog — `catalog_overview · find_places · find_photos · get_trips ·
place_stats · busiest_days` — so a chat client (ChatGPT, Claude) can answer
questions about the user's travels. Tool logic is plain functions taking a
`Database` (tested directly in `tests/test_mcp_server.py`); `create_server()`
wraps them. Queries go through `Database.search_located/search_places/
catalog_summary`; `get_trips` reuses `timeline.build_stays`. Results are capped
and sampled to stay small, and carry `google_photos_url` + a Google Maps link —
no images.

Locally, `track-me mcp --stdio` serves a desktop client with no auth, reading
`./userdata` by default. Every call opens a fresh **read-only, immutable**
connection (`mcp_server._open_db`) — no SQLite locks, so it never blocks or is
blocked by ingest/geocode.

Over HTTP it requires **Google OAuth** (FastMCP's OAuth proxy) and
`AllowlistGoogleProvider` rejects any token whose verified email is not in
`MCP_ALLOWED_EMAILS` — keep it fail-closed. `--no-auth` is refused off loopback.
Keep every tool read-only. Deployment runbook: `DEPLOY.md`.

**Client support (verified against OpenAI's help center, 2026-10-31):** the Cloud Run
server works with Claude desktop/web/mobile as a custom connector. ChatGPT cannot use
custom MCP servers on mobile ("web only"), and on web only on Business/Enterprise/Edu
(Pro: read/fetch). Don't promise ChatGPT mobile support. Details and caveats (incl.
`offline_access`) are in `README.md` → "Which clients work".

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

## Worker VM

Takeout fetching/cataloging runs on a GCP worker VM named `tracker` (project
`vino9-276317`, zone `us-west1-b`, `ssh tracker`; settings in
`~/.config/station-vm/tracker.env`). Manage it with two skills from the
`my-workstation` plugin — don't hand-roll `gcloud` scripts in this repo:

- **`my-workstation:provision-vm`** — create a VM, or import/register an existing one
  (writes the env file and the `ssh tracker` alias).
- **`my-workstation:vm-lifecycle`** — everything after that, via
  `/my-workstation:vm-lifecycle tracker <action>` (user-invoked only; destructive
  actions print a plan and need an explicit `--yes`):
  `seed` (snapshot the root disk) · `create` (recreate from the seed) ·
  `add scratch [GB]` · `delete scratch` · `shelve` · `destroy`.

The Takeout skills expect a scratch disk at `/mnt/scratch` (`add scratch`, default
150 GB; it is never snapshotted and is lost on `delete scratch`/`shelve --delete`).

Examples:
- "Use my-workstation:provision-vm to import my existing GCP VM `tracker` and write its env."
- `/my-workstation:vm-lifecycle tracker add scratch 150` — attach + mount the scratch disk.

## Git Commit Guidelines

**Single-developer repo — work directly on `main`.** There is no feature-branch
workflow, no pull-request review requirement, and no branch protection to honor.
Commit changes straight to `main`. Don't create feature branches or open PRs just
to land a change (a branch is only warranted for genuinely experimental work the
developer asks to keep separate).

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
