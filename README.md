# track_me

A local-first tool that turns **Google Takeout** photo exports into a clean,
queryable travel timeline. It parses each photo's Takeout sidecar JSON (plus EXIF)
for an authoritative timestamp and location, reverse-geocodes coordinates into
place names, and keeps a deep link back to the original on Google Photos. The
Takeout extract itself is treated as transient — everything is re-derivable, so
the local SQLite catalog is the source of truth you keep.

> **Status:** mid re-architecture. The catalog + ingestion + geocoding pipeline
> (the `library` and `places` apps) is built and working. The spot-check/geotag UI
> is **planned but not started** — see `REARCH_PLAN.md` and `docs/PHASE_3_UI_PLAN.md`.
> A legacy `myphoto` app still serves the old UI and is slated for removal.

## Stack

| Tool | Purpose |
| --- | --- |
| [Django](https://www.djangoproject.com/) 5 | app framework, ORM, management commands |
| [django-ninja](https://django-ninja.dev/) + Pydantic | typed API at `/api` (auto docs at `/api/docs`) |
| [SQLite](https://www.sqlite.org/) (local-first) | catalog at `data/track_me.db`; Postgres via `DATABASE_URL` |
| [H3](https://h3geo.org/) | spatial cells for batched geocoding + clustering |
| [uv](https://docs.astral.sh/uv/) | dependency + virtualenv management |
| [ruff](https://docs.astral.sh/ruff/) | lint + format |
| [ty](https://github.com/astral-sh/ty) | type checking (**not** pyright) |

Requires **Python 3.12+**.

## Setup

```shell
uv sync                                   # create venv + install deps
echo "GOOGLE_MAPS_API_KEY=..." > .env     # only needed for the geocode step
python manage.py migrate                  # create the fresh schema (data/track_me.db)
```

The `data/` directory (SQLite db + optional thumbnails) is auto-created at startup
and gitignored.

## The pipeline

```shell
# 1. INGEST a Takeout extract: parse sidecars + EXIF, set taken_at for every item,
#    resolve location, store the Google Photos link, derive a local timezone.
#    Thumbnails are opt-in (--thumbnails); the timeline doesn't need them.
python manage.py ingest /path/to/takeout-extract [--thumbnails] [--reprocess]

# 2. GEOCODE located items into place names + country codes (H3-batched, Google API).
python manage.py geocode [--resolution 9] [--max-api-calls N] [--recalculate]
python manage.py geocode --estimate        # count API calls / cost without calling

# 3. EXPORT located media as a timestamped track for timeline tools.
python manage.py export_gpx [--format gpx|geojson] [--year YYYY] [--output FILE]
```

`ingest` and `geocode` are **re-runnable and incremental**: already-seen items
(matched by `MediaItem.dedupe_key`) are skipped, and manual edits are never
overwritten.

## Development server

Tailwind CSS must be compiled before serving (the UI is broken without it):

```shell
python manage.py tailwind build           # compile once
python manage.py tailwind runserver       # compile + watch + runserver
```

- API docs (auto-generated): http://localhost:8000/api/docs

## Quality

Run on Python files after changes (see `CLAUDE.md` for the full workflow):

```shell
ruff format <file.py>
ruff check . --fix
ruff check .
pytest
ty check .
```

## More

- **`CLAUDE.md`** — working agreement for coding agents (commands, conventions).
- **`REARCH_PLAN.md`** — the overall rebuild plan (Phases 0–4).
- **`docs/PHASE_3_UI_PLAN.md`** — the planned spot-check UI (on hold).
