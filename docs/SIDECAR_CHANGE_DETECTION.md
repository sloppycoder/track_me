# Sidecar change detection on re-import

**Status:** planned (not yet implemented). Design only — code lands later.

## Problem

The ingest pipeline is idempotent by `MediaItem.dedupe_key`, a *content
identity* (which photo is this), **not** a *content hash* (what does its
metadata say). The strongest available signal wins
(`library/ingest/pipeline.py::compute_dedupe_key`): Google Photos URL → title +
capture epoch → EXIF datetime + perceptual hash → filename + size.

On re-import, `_ingest_one` does a **fast skip**: if a sidecar-derived key
already maps to a *complete* item (has `taken_at`, thumbnail present when
enabled), it returns **before reading the new sidecar content**
(`pipeline.py:170-183`). That makes re-running cheap, but it means edits to an
already-ingested sidecar are silently ignored.

This breaks a core workflow: **lots of photos are imported untagged, then tagged
in batch in Google Photos (location, title), then re-exported and re-imported.**
For a URL-keyed photo the key never changes, so the new GPS / title is dropped
unless you pass `--force` (which re-decodes the *entire* library).

## Goal

Detect when the **meaningful** sidecar fields changed for an already-ingested
item, and re-apply just those — while:

- keeping the cheap fast-skip for the (vast majority of) unchanged items,
- preserving the existing `MANUAL` overrides for time and location,
- not requiring `--force` / a full-library re-decode.

## Design: a sidecar *content* fingerprint

Keep `dedupe_key` as the identity. Add a second hash — `sidecar_fingerprint` —
over only the fields that matter, and change the skip rule to:

> skip only if the item is **complete** *and* its `sidecar_fingerprint` is
> **unchanged**.

If the fingerprint differs, fall through to the existing update path. One extra
hash comparison per file; unchanged items still fast-skip, only re-tagged items
fall through and re-decode.

### Which fields count (some matter more than others)

The fingerprint covers only the **significant** set — fields that get *filled in
later* in the tagging workflow — so noise elsewhere never triggers a re-decode:

- `geoData` / `geoDataExif` coordinates (GPS tagging) — **primary**
- `title`, `description` (renames / captions)

Deliberately **excluded:**

- **`photoTakenTime` / capture epoch.** It rarely changes (only via Google
  Photos' explicit "Edit date & time"), and it's *redundant* with the identity:
  for title+epoch–keyed items the epoch is already part of `dedupe_key`, so a
  change there produces a **new row**, not an update — the fingerprint never
  runs. It would only ever catch a date edit on a *URL-keyed* photo, a narrow
  case. Leave it out; adding it back later is a one-line change.

Each significant field already has its own apply-logic with a `MANUAL` guard
(`pipeline.py:230, 236`). So the fingerprint only decides **whether to enter**
the update path; the existing guards decide **what actually changes**. Clean
separation — no per-field merge logic needed.

## Implementation sketch

**1. Model — one nullable column** (`library/models.py`, near `dedupe_key`):

```python
sidecar_fingerprint = models.CharField(
    max_length=64, null=True, blank=True,
    help_text="Hash of significant sidecar fields; detects re-tagging on re-import",
)
```

**2. Fingerprint helper** (`pipeline.py`, beside `compute_dedupe_key`):

```python
def compute_sidecar_fingerprint(sidecar: Sidecar | None) -> str | None:
    if sidecar is None:
        return None
    coords = sidecar.coords()
    parts = [
        (sidecar.title or "").strip().lower(),
        (sidecar.description or "").strip().lower(),
        f"{coords[0]:.6f},{coords[1]:.6f}" if coords else "",
    ]
    return _sha1(["sidecar", *parts])
```

**3. Skip predicate** — fold the fingerprint into both skip checks
(`pipeline.py:181` and `:208`). Compute `fingerprint` once at the top of
`_ingest_one` and pass it in:

```python
def _can_skip(self, item, kind, fingerprint) -> bool:
    return (
        item.taken_at is not None
        and self._thumb_ok(item, kind)
        and item.sidecar_fingerprint == fingerprint
    )
```

Orphans (no sidecar) get `None == None` → still skip, unchanged behavior.

**4. Persist it** on the update path (`pipeline.py:220`, beside
`item.sidecar_raw = ...`):

```python
item.sidecar_fingerprint = fingerprint
```

**5. Stats** — add a `refreshed` counter to `IngestStats` so a run distinguishes
new (`created`) from re-applied (`refreshed`) items.

No new CLI flag: fingerprint change-detection becomes the **default** (cheap and
correct). `--force` keeps its meaning — ignore all skips, re-do everything.

## Geocode invalidation on coordinate change (required companion change)

Ingest writes only identity/file/time/location/exif fields — it **never**
touches `place_label`, `country_code`, or `geocoded_at` (owned by
`places/geocode.py:169-172`). So re-import does **not** overwrite existing
geocoding. Two cases:

- **Unlocated → now has GPS:** `geocoded_at` is still `NULL`, so the next
  `geocode` run picks it up normally. ✅
- **GPS *changed*:** ingest updates the coordinates, but `geocoded_at` stays set,
  and `geocode` is incremental (`geocode.py:38` filters
  `geocoded_at__isnull=True`). So the next run **skips it** and the label stays
  pointing at the *old* location — stale, silently. ⚠️

Fix: when ingest meaningfully *moves* an item's coordinates, invalidate the
stale geocode so incremental `geocode` re-does just those items. Around
`set_location` (`pipeline.py:236-244`):

```python
old_cell = item.h3_cell                # capture before set_location
...
item.set_location(...)
if item.h3_cell != old_cell:           # coords moved (res-11 ~25 m)
    item.place_label = ""
    item.country_code = None
    item.geocoded_at = None            # re-queues for incremental geocode
```

Comparing `h3_cell` instead of raw lat/lon avoids re-geocoding on sub-meter
float jitter. Do the same on `clear_location` (GPS removed).

## Backfilling the existing ~6000 items

The fingerprint can be backfilled **from data already in the table** — no need
to read the original Takeout JSON files. The full sidecar dump is persisted in
`MediaItem.sidecar_raw` (`pipeline.py:220`), and the fingerprint is computed
purely from `title` / `description` / coords, all of which live there. Because
`sidecar_raw` *is* the dump of the originally-parsed sidecar, recomputing from it
yields the identical fingerprint a fresh parse would — which is what makes the
future skip-comparison correct.

Two-step rollout:

1. **Schema migration** — add the nullable column. Instant; existing rows get
   `NULL`.
2. **Backfill** — a data migration that reconstructs each `Sidecar` from
   `sidecar_raw` and stores `compute_sidecar_fingerprint(...)`. Rows with
   `sidecar_raw = None` (orphans) correctly stay `NULL`.

```python
def backfill(apps, schema_editor):
    from library.ingest.pipeline import compute_sidecar_fingerprint
    from library.ingest.sidecar import Sidecar

    MediaItem = apps.get_model("library", "MediaItem")
    to_update = []
    for item in MediaItem.objects.exclude(sidecar_raw=None).iterator():
        sidecar = Sidecar.model_validate(item.sidecar_raw)
        item.sidecar_fingerprint = compute_sidecar_fingerprint(sidecar)
        to_update.append(item)
    MediaItem.objects.bulk_update(
        to_update, ["sidecar_fingerprint"], batch_size=500
    )
```

**Is the backfill optional?** Yes, but recommended. Without it, all existing rows
stay `NULL`; on the next re-import every one sees `NULL != <hash>`, fails the
skip, and **re-decodes EXIF once** before getting stamped — a full-library decode
you'd otherwise avoid. The backfill is pure DB work (no decode, no file I/O), so
run it.

**Migration caveat:** importing app code (`compute_sidecar_fingerprint`,
`Sidecar`) into a migration is convenient but slightly fragile if the formula
later changes. For a single personal DB that runs the migration once, it's fine;
to be bulletproof, inline the hash logic or run the backfill as a one-off
`manage.py` command instead.

## Known limitations

- **Title change on a non-URL photo creates a duplicate, not an update.** For
  title+epoch–keyed items, `title` is *part of* `dedupe_key`
  (`pipeline.py:72`), so a rename changes the identity → new row. The
  fingerprint can't fix this — it's structural. URL-keyed photos update cleanly.
  If renames on URL-less photos are common, demote `title` in the key tiers.
- **`title` / `description` aren't first-class columns** — they live only inside
  `sidecar_raw`, so "re-import picks up the new title" currently just refreshes
  `sidecar_raw`; nothing displays it. If captions should surface, promote them to
  real columns with a `MANUAL` guard like time/location.
- **EXIF GPS outranks sidecar `geoData`** (`pipeline.py::_decide_coords`). A
  sidecar location tag applies only when the file has no EXIF GPS.

## Implementation checklist

- [ ] `sidecar_fingerprint` column + schema migration
- [ ] `compute_sidecar_fingerprint` helper
- [ ] fold fingerprint into the skip predicate (both skip points)
- [ ] persist fingerprint on the update path
- [ ] `refreshed` stat in `IngestStats`
- [ ] geocode invalidation on coordinate change (`set_location` / `clear_location`)
- [ ] backfill data migration for existing rows
- [ ] regression test (fixture: same identity, changed `geoData`/`title` →
      refreshed, not skipped; unchanged → skipped) in `tests/fixtures/`
