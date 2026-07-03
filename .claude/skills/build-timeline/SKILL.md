---
name: build-timeline
description: >
  Construct a travel timeline (chronological, non-overlapping distinct
  trips/visits with date ranges) by querying the photo catalog in
  data/track_me.db, refine it conversationally over several turns, and — ONLY
  after the user confirms — write it to userdata/timelines/<id>.json for the
  Google Maps viewer. Use whenever the user wants to build, refine, or save a
  travel/trip timeline of countries or cities they visited and when.
---

# Build a travel timeline

Turn the photo catalog into a **timeline of distinct trips/visits**, iterate with
the user until they're happy, then persist it as JSON for the map viewer.

## Workflow (the important part)

1. **Clarify scope** if unstated: time window, granularity (**country** or
   **city**), and any region filter. Pick sensible defaults and say what you chose.
2. **Draft — do NOT write a file yet.** Build the timeline and show the user the
   preview table. Note that day boundaries use the photo's **local** timezone and
   ordering is UTC.
3. **Refine over as many turns as needed.** The user may: merge/split stays,
   relabel a place, change granularity, adjust the city merge radius, or restrict
   the window/region. Re-draft and show the new preview each time. **Keep the
   running list of the user's timeline prompts** — it goes into the JSON.
4. **Write ONLY on explicit confirmation** ("looks good", "save it", "yes"). Never
   write `userdata/timelines/<id>.json` before the user approves the current draft.
5. Tell the user it's saved and how to view it: `track-me serve` →
   `http://localhost:5000`.

**Confirmation gate is the core contract of this skill: preview first, write last.**

## Tool: `track-me timeline`

Preview (no file written) — show this to the user:

```bash
track-me timeline --start 2019-01-01 --end 2020-01-01 --level country
track-me timeline --start 2023-06-01 --end 2023-07-01 --level city \
    --region ES FR --merge-km 40
```

Persist ONLY after the user confirms, passing the full prompt trail:

```bash
track-me timeline --start 2019-01-01 --end 2020-01-01 --level country \
    --write --id countries-2019 --title "Countries visited in 2019" \
    --prompt "build a country-level timeline of 2019" \
    --prompt "merge the two Singapore stays"
```

For per-stay hand-tuning (merge stays 3–4, relabel) that flags can't express,
import the library, mutate the `stays` list, then write:

```python
from track_me import timeline as tl
stays = tl.build_stays("2019-01-01", "2020-01-01", level="country")
# ...edit stays in place...
tl.write_timeline(tl.to_document(stays, timeline_id="...", title="...", prompts=[...]))
```

## The recipe (implemented in `src/track_me/timeline.py`)

1. **Pull located photos in time order** for the window (`load_points`); order by
   `taken_at` (UTC), bucket by the photo's **local day** (`local_date`).
2. **Pick the place label per photo:**
   - *Country-level* — `place.country_code` (via the media→place join).
   - *City-level* — the stored **`place.city`** (Google-derived at geocode time
     using a priority fallback chain). No query-time reverse-geocoding.
3. **Segment** into contiguous runs; start a new trip when the label changes.
4. **Smooth** border/blip noise: absorb any run < 24h bracketed by the same label
   on both sides, then re-coalesce; repeat to a fixed point.
5. **City-level only — collapse metros by proximity:** cluster consecutive photos
   within `--merge-km` (~50 km) of a running centroid into one stay, labelled by
   the most common `place.city` in the cluster.

Output: chronological, **non-overlapping** date ranges; a revisited place appears
once per distinct visit.

## Output schema (`userdata/timelines/<id>.json`)

```jsonc
{
  "id": "europe-2019",
  "title": "Europe, summer 2019",
  "prompts": ["<every timeline prompt the user gave, in order>"],
  "generated_at": "2026-07-03T14:20:00+00:00",
  "stays": [
    { "label": "Paris, FR", "from": "2019-06-02", "to": "2019-06-09",
      "lat": 48.857, "lng": 2.352, "photo_count": 143,
      "sample_url": "https://photos.google.com/..." }
  ]
}
```

`stays` must be chronological and non-overlapping. `lat`/`lng` are the stay
centroid (the map marker). `prompts` exists for reproducibility. The viewer reads
these files and injects the Google Maps key from `.env`; it never touches the DB.
Prerequisite: the catalog must be geocoded (`track-me geocode`) for `place.city` /
`country_code` to be populated.
