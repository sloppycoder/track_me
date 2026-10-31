---
name: takeout-catalog
description: Catalog Google Takeout export batches stored in Google Drive (the root of the `gdrive:` remote, which is locked to the Takeout folder) into CATALOG.md - what each contains (Google Photos, Google Health / Fitbit, or other), the date range it covers, and whether all its parts are present. Meant to be run ON the processing VM (see vm-tracker), not on a laptop. Use when asked to catalog/refresh/audit the Takeout zips.
---

# Catalog Google Takeout batches

Runs on the processing VM, where `rclone` is configured with a `gdrive:` remote.
If you are on a laptop, stop: create the VM with `vm-tracker` first.

## Drive access: always the root of `gdrive:`

`rclone.conf` sets `root_folder_id` to the Takeout folder, so **the root of `gdrive:`
*is* the Takeout folder** and rclone cannot see anything else in the Drive. That is
deliberate, to prevent accidental changes to other files. Therefore:

- Always use bare `gdrive:` paths (`gdrive:`, `gdrive:CATALOG.md`,
  `gdrive:takeout-...zip`). Never write `gdrive:Takeout/...`; under the restricted
  root that is a different (probably non-existent) subfolder.
- **Preflight (do this first, every run):** `rclone lsf gdrive: --format sp`. If it
  errors, or no file matching `^takeout-\d{8}T\d{6}Z-(\d+-)?\d{3}\.zip$` is
  listed in the root, **abort**: print what was listed and tell the user the
  `root_folder_id` / `rclone.conf` may be wrong. Do not fall back to
  `gdrive:Takeout`, do not search other folders, and do not touch `CATALOG.md`.
- Only `CATALOG.md` (and scratch files under `$SCRATCH`) may be written. Never
  delete, move or rename anything on `gdrive:`.

## Naming convention (verified against the Takeout folder on 2026-10-31)

```
takeout-<YYYYMMDDTHHMMSSZ>-001.zip        # exactly one per batch; small (80 KB-350 KB)
takeout-<YYYYMMDDTHHMMSSZ>-1-001.zip      # data parts: -1-001, -1-002, ... (~2 GB each)
```

- There is exactly one `-001.zip` (the index) per batch. The data parts are
  numbered `-1-NNN` **starting at `-1-001`**, so `-1-001.zip` also exists. Match the index file with the regex `^takeout-(\d{8}T\d{6}Z)-001\.zip$`
  (no `-1-` segment). Do not treat `-1-001.zip` as a batch.
- **Timestamps can differ by a second between the index and its parts**
  (`...T042324Z-001.zip` vs `...T042325Z-1-0NN.zip`). Group parts to a batch by the
  nearest index timestamp within ~5 s, never by exact string equality.
- Record the part count you actually see. A batch can legitimately have a single
  part: `...T124003Z` (Google Health, mostly CSV) is 5.37 GB uncompressed but fits
  in one 382 MB `-1-001.zip`.
- Full parts are ~2.13-2.15 GB, and a non-final part can be a little smaller
  (e.g. 2.128 GB). The **final** part holds the remainder and is usually well under
  2 GB (seen: 1.51 GB, 1.41 GB, 0.38 GB).

## Catalog file

`gdrive:CATALOG.md` (root of the remote, next to the zips) is the source of truth.
It may not exist yet; create it
with the header below. Always pull, edit, push:

```bash
# /mnt/scratch exists only while a scratch disk is attached; the catalog needs
# just a few MB, so fall back to the home directory.
if mountpoint -q /mnt/scratch; then D=/mnt/scratch/tmp; else D=$HOME/scratch/takeout; fi
SCRATCH=${SCRATCH:-$D}; mkdir -p "$SCRATCH"
rclone copyto gdrive:CATALOG.md "$SCRATCH/CATALOG.md" 2>/dev/null || true
# ...edit...
rclone copyto "$SCRATCH/CATALOG.md" gdrive:CATALOG.md
```

Format: one table row per batch, keyed by the index zip file name.

```markdown
# Takeout catalog

| Index file | Date range | Parts | Total size | Content | Complete | Status | Last checked |
|---|---|---|---|---|---|---|---|
| takeout-20260820T042324Z-001.zip | 2000–2025 | 48 | 102.4 GB | Google Photos | yes | present | 2026-10-31 |
| takeout-20261013T124003Z-001.zip | 2021-09 – 2026-10 | 1 | 0.4 GB | Google Health / Fitbit | yes | present | 2026-10-31 |
```

The export time is already in the index file name, so there is no separate column.

- `Total size`: index zip + all its parts, as listed on Drive, in decimal GB (1 dp).
- `Date range`: from `archive_browser.html` (see step 4). Use `YYYY–YYYY` (or a
  single `YYYY`) when only years are known, `YYYY-MM – YYYY-MM` when months are.
  Leave it blank if nothing reliable is listed.
- `Complete`: from part sizes alone; never read or download part contents.
  - `yes`: parts are numbered `-1-001` … `-1-NNN` with no gaps, every part is
    larger than 0 bytes, and the last part is smaller than 2,000,000,000 bytes
    (so it is the final remainder, not a full part with more after it).
  - `no (missing -1-007, -1-012)` / `no (-1-003 is 0 bytes)`: if there's a gap or
    an empty part.
  - `unknown (last part full-size)`: no gap, but the last part is ≥ 2,000,000,000
    bytes, so more parts may be missing at the end.

- `Content`: `Google Photos`, `Google Health / Fitbit`, both comma-separated, or
  `other` for any product that is neither (add a few words only if obvious from the
  listing, e.g. `other (Drive)`). Don't dig further.
- `Status`: `present` or `deleted` (see below).
- Preserve any hand-written notes/columns the user added; never reorder or drop rows.

## Procedure

1. **List the Drive folder once** with sizes:
   `rclone lsf gdrive: --format sp` (size;path), after the preflight above. Build the set of index files
   (regex above) and assign each data part to its batch (nearest timestamp, ≤5 s).
   Ignore everything that isn't a `takeout-*.zip` (e.g. `CATALOG.md`).
2. **Pull `CATALOG.md`** (above). Parse the existing rows.
3. **For each index file already in the catalog and still listed:** don't
   re-download it; just refresh `Parts`/`Total size`/`Complete` from the listing,
   and `Last checked`.
4. **For each new index file:**
   ```bash
   D=$SCRATCH/<timestamp>; mkdir -p "$D"
   rclone copy gdrive:<index-file> "$D"
   unzip -l "$D/<index-file>"
   ```
   Decide `Content` from the listing: paths under `Takeout/Google Photos/` → Google
   Photos; `Takeout/Google Health/` (product `FITBIT`), `Takeout/Fitbit/`,
   `Takeout/Fit/` or `Google Fit` → Google Health / Fitbit; anything else → `other`.
   In practice the index zip holds only `Takeout/archive_browser.html`. Extract it
   (`unzip -o -d "$D" ... 'Takeout/archive_browser.html'`) and read the products
   from it, rather than downloading 2 GB data parts:
   `grep -oE 'data-english-name="[^"]+" data-folder-name="[^"]+"'` gives one line
   per product (seen: `PHOTOS`/`Google Photos`, `FITBIT`/`Google Health`). Only
   download a `-1-001.zip` and `unzip -l` it if neither the listing nor
   `archive_browser.html` answers the question.

   **Date range** (strip tags and `html.unescape` the HTML first, then read the
   text):
   - Google Photos: the folder list has one `Photos from YYYY` folder per year,
     so take the min and max year. **Years only:** each folder shows just a few
     sample file names, so don't infer months from them. Ignore album folders
     (`Archive`, `trip log`, …). Gaps are normal (e.g. no 2001–2002), and an old
     year like `Photos from 2000` is usually photos with bad dates. Record what's
     listed anyway.
   - Google Health / Fitbit: every file is listed, and many names carry a date
     (`Active Zone Minutes - 2023-01-01.csv`). Take the min/max of
     `\b20\d{2}-\d{2}-\d{2}\b` across the text and record `YYYY-MM – YYYY-MM`.
   - Anything else: leave blank unless the dates are obvious.
   Add the row; delete the scratch dir afterwards (`rm -rf "$D"`).
5. **Deleted batches:** any catalogued index file that is no longer in the Drive
   listing → set `Status` to `deleted`. **Never remove the row.** If a `deleted` file
   reappears, set it back to `present`.
6. **Push `CATALOG.md`** back to `gdrive:CATALOG.md`, then print a summary:
   new batches added, marked deleted, unchanged, and the current table.

## Rules

- Only the small `-001.zip` index files are downloaded by default. Never pull the
  multi-GB parts unless step 4 says it's unavoidable.
- Don't unzip data parts or touch the track_me database; this skill only catalogs
  (downloading + unzipping a batch is the `takeout-fetch` skill).
- Never delete anything from Drive.
