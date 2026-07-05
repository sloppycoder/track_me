# track_me

A local-first tool that turns **Google Takeout** photo exports into a clean,
queryable travel timeline, then visualizes it on **Google Maps**. It parses each
photo's Takeout sidecar JSON (plus EXIF) for an authoritative timestamp and
location, reverse-geocodes coordinates into place names, and keeps a deep link
back to the original on Google Photos. The Takeout extract is treated as
transient — everything is re-derivable, so the local SQLite catalog is the source
of truth you keep.

## Stack

| Tool | Purpose |
| --- | --- |
| Python 3.12 + stdlib `sqlite3` | catalog + data layer (no ORM) |
| [Flask](https://flask.palletsprojects.com/) | the Google Maps timeline viewer |
| [H3](https://h3geo.org/) | spatial cells for batched geocoding + clustering |
| Google Maps API | reverse geocoding + the map viewer |
| [uv](https://docs.astral.sh/uv/) | dependency + virtualenv management |
| [ruff](https://docs.astral.sh/ruff/) / [ty](https://github.com/astral-sh/ty) | lint + format / type checking |

Requires **Python 3.12+**. Not Django; not a web app — a CLI plus a tiny local
viewer. All code lives under `src/track_me/`.

## Setup

```shell
uv sync                                   # create venv + install deps (+ the `track-me` CLI)
echo "GOOGLE_MAPS_API_KEY=..." > .env     # needed for the geocode step + the viewer
```

Local state (SQLite DB, thumbnails, timelines) lives under `userdata/` and is
gitignored. Point it elsewhere with `TRACKME_USERDATA` (default `./userdata`).
The schema is created automatically on first use — no migrations.

## The pipeline

```shell
# 1. INGEST a Takeout source (local dir or s3://bucket/prefix): match sidecars,
#    set taken_at + local_date + timezone, resolve location, store the Photos link.
#    Parallel + sidecar-first (reads image bytes only when the sidecar lacks data).
track-me ingest <source> [--thumbnails] [--force] \
                 [--filter YYYY-MM[,YYYY-MM]] [--workers 32]

# 2. GEOCODE located items into place names (H3-batched Google calls). Fetch stores
#    the raw response; derive picks city/admin1 offline (re-runnable, free).
track-me geocode [--resolution 9] [--max-api-calls N] [--recalculate]
track-me geocode --estimate       # count API calls / cost without calling
track-me geocode --derive-only    # recompute city/admin1 from stored responses

# 3. EXPORT located media as a timestamped track for other timeline tools.
track-me export [--format gpx|geojson] [--year YYYY] [--output FILE]
```

`ingest` and `geocode` are **re-runnable and incremental**: already-seen items
(matched by `dedupe_key`) are skipped and manual edits are never overwritten.

## Build & view a travel timeline

```shell
# Build a timeline (preview; --write persists to userdata/timelines/<id>.json).
track-me timeline --start 2019-01-01 --end 2020-01-01 --level country
track-me timeline --start 2019-01-01 --end 2020-01-01 --level country \
    --write --id countries-2019 --title "Countries visited in 2019"

# Launch the Google Maps viewer at http://localhost:5000.
track-me serve
```

`--write` also embeds a compact per-photo **points** payload in the JSON so the
viewer can re-cluster on the fly; pass `--no-points` to omit it for a lighter file.

The viewer is interactive: a time-range slider (with a photo-density histogram)
under the map lets you scrub to any window, and the map POIs adapt from **country
→ city → neighborhood** as you narrow the range (or force a level with the
Auto / Country / City / Area toggle).

You can also build timelines from the browser: open `http://localhost:5000/build`,
drag the time-range slider, toggle Country/City, tune the merge/smoothing knobs
against a live map preview, then **Save**. It calls the same engine as
`track-me timeline`, so the saved JSON is identical to the CLI for the same knobs.

## Quality

```shell
ruff format <file.py>
ruff check . --fix
ruff check .
pytest
ty check .
```

## More

- **`CLAUDE.md`** — working agreement for coding agents (structure, commands).
