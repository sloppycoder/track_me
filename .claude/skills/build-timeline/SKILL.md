---
name: build-timeline
description: >
  Construct a travel timeline (chronological, non-overlapping distinct
  trips/visits with date ranges) by querying the photo catalog in
  data/track_me.db, refine it conversationally over several turns, and — ONLY
  after the user confirms — write it to userdata/timelines/<id>.json for the Google Maps
  viewer. Use whenever the user wants to build, refine, or save a travel/trip
  timeline of countries or cities they visited and when.
---

# Build a travel timeline

Turn the photo catalog into a **timeline of distinct trips/visits**, iterate with
the user until they're happy, then persist it as JSON for the map viewer.

## Workflow (the important part)

1. **Clarify scope** if unstated: time window, granularity (**country** or
   **city**), and any region filter. Pick sensible defaults and say what you chose.
2. **Draft — do NOT write a file yet.** Build the timeline (see recipe below) and
   show the user a preview table (`timeline_lib.preview`). Note that day
   boundaries use the photo's **local** timezone and ordering is UTC.
3. **Refine over as many turns as needed.** The user may: merge/split stays,
   relabel a place, change granularity, adjust the city merge radius, or restrict
   the window/region. Re-draft and show the new preview each time. **Keep the
   running list of the user's timeline prompts** — it goes into the JSON.
4. **Write ONLY on explicit confirmation** ("looks good", "save it", "yes"). Never
   write `userdata/timelines/<id>.json` before the user approves the current draft. On
   confirmation, call `write_timeline` with `prompts=` set to the full trail of
   the user's refinement prompts and a stable, kebab-case `id`.
5. Tell the user it's saved and that they can view it at
   `python viewer/app.py` → `http://localhost:5000`.

**Confirmation gate is the core contract of this skill: preview first, write last.**

## The recipe (from CLAUDE.md — keep in sync)

Validated against `data/track_me.db`:

1. **Pull located photos in time order.** Filter to the requested window, require
   coordinates, order by `taken_at`. Use `taken_at` (UTC) for ordering; day
   boundaries are the photo's local day (offer UTC if it matters).
2. **Pick the place label per photo:**
   - *Country-level* — use `country_code` directly.
   - *City-level* — do NOT parse `place_label` (it's a full formatted address; the
     city sits at a different comma-index per country). Reverse-geocode the
     coordinates **offline** with the `reverse_geocoder` package (a project
     dependency; GeoNames nearest-city, no API key/network), keyed as
     `name, admin1 (cc)`. There is no stored `city` column — derive at query time.
3. **Segment into contiguous runs:** walk the ordered list, start a new trip each
   time the label changes; `from`/`to` = first/last of the run.
4. **Smooth border/blip noise:** absorb any run lasting **< 24h that is bracketed
   by the same label on both sides** (same-day border crossings, single stray
   photos), then re-coalesce adjacent same-label runs. Repeat to a fixed point.
5. **City-level only — collapse metros by proximity:** raw GeoNames names
   over-split a metro into neighborhoods. Cluster consecutive photos within
   **~50 km** (haversine vs running centroid) into one stay, label it by the *most
   common* city in the cluster. The radius is a knob — bigger merges adjacent
   cities, smaller splits metros into districts.

Output: chronological, **non-overlapping** date ranges; list a place once per
distinct visit (a revisited city appears multiple times). Ignore photo counts
unless asked. Day-trips from a base city legitimately appear as their own stays.

`scripts/timeline_lib.py` implements all of the above — prefer it over
re-deriving. Steps 1–5 map to `load_points` → `country_stays` / `city_stays`.

## Tools

- **Common path (CLI):** `scripts/build_timeline.py` — preview by default; add
  `--write --id --title --prompt ...` to persist after confirmation.
  ```bash
  cd .claude/skills/build-timeline/scripts
  python build_timeline.py --start 2019-01-01 --end 2020-01-01 --level country
  ```
  Run with the project venv (`uv run python ...` or `.venv/bin/python`).
- **Refinements (library):** for per-stay edits (merge stays 3–4, relabel,
  hand-tune), import `timeline_lib`, build `stays`, mutate the list in a short
  snippet, then
  `write_timeline(to_document(stays, timeline_id=..., title=..., prompts=[...]))`.

## Output schema (`userdata/timelines/<id>.json`)

```jsonc
{
  "id": "europe-2019",
  "title": "Europe, summer 2019",
  "prompts": ["<every timeline prompt the user gave, in order>"],
  "generated_at": "2026-07-03T14:20:00Z",
  "stays": [
    { "label": "Paris, FR", "from": "2019-06-02", "to": "2019-06-09",
      "lat": 48.857, "lng": 2.352, "photo_count": 143,
      "sample_url": "https://photos.google.com/..." }
  ]
}
```

`stays` must be chronological and non-overlapping. `lat`/`lng` are the stay
centroid (the map marker). `prompts` exists for reproducibility — always include
the actual user prompts that produced the timeline. The `viewer/` Flask app reads
these files and injects the Google Maps key from `.env`; it never touches the DB.
