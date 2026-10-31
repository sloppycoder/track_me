---
name: takeout-catalog
description: Catalog Google Takeout export batches stored in Google Drive (the root of the `gdrive:` remote, which is locked to the Takeout folder) into CATALOG.md - when each was exported and what it contains (Google Photos, Google Fit, or other). Meant to be run ON the processing VM (see vm-tracker), not on a laptop. Use when asked to catalog/refresh/audit the Takeout zips.
---

# Catalog Google Takeout batches

Runs on the processing VM, where `rclone` is configured with a `gdrive:` remote.

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
(If you are on a laptop, stop: create the VM with `vm-tracker` first.)

## Naming convention (verified against the Takeout folder on 2026-10-31)

```
takeout-<YYYYMMDDTHHMMSSZ>-001.zip        # exactly one per batch; small (80 KB-350 KB)
takeout-<YYYYMMDDTHHMMSSZ>-1-001.zip      # data parts: -1-001, -1-002, ... (~2 GB each)
```

- The user's rule "one `-001.zip` per batch" holds: 3 batches, 3 such files. But the
  data parts are numbered `-1-NNN` **starting at `-1-001`**, so `-1-001.zip` also
  exists. Match the index file with the regex `^takeout-(\d{8}T\d{6}Z)-001\.zip$`
  (no `-1-` segment). Do not treat `-1-001.zip` as a batch.
- **Timestamps can differ by a second between the index and its parts**
  (`...T042324Z-001.zip` vs `...T042325Z-1-0NN.zip`). Group parts to a batch by the
  nearest index timestamp within ~5 s, never by exact string equality.
- A batch may have only part of its files present (e.g. `...T124003Z` has just
  `-001` and `-1-001`). Record the part count you actually see.

## Catalog file

`gdrive:CATALOG.md` (root of the remote, next to the zips) is the source of truth.
It may not exist yet; create it
with the header below. Always pull, edit, push:

```bash
SCRATCH=${SCRATCH:-$HOME/scratch/takeout}; mkdir -p "$SCRATCH"
rclone copyto gdrive:CATALOG.md "$SCRATCH/CATALOG.md" 2>/dev/null || true
# ...edit...
rclone copyto "$SCRATCH/CATALOG.md" gdrive:CATALOG.md
```

Format: one table row per batch, keyed by the index zip file name.

```markdown
# Takeout catalog

| Index file | Exported (UTC) | Parts | Total size | Content | Status | Last checked |
|---|---|---|---|---|---|---|
| takeout-20260820T042324Z-001.zip | 2026-08-20 04:23:24 | 48 | 100.3 GB | Google Photos | present | 2026-10-31 |
```

- `Content`: `Google Photos`, `Google Fit`, both comma-separated, or `other` (add a few
  words only if obvious from the listing, e.g. `other (Drive)`). Don't dig further.
- `Status`: `present` or `deleted` (see below).
- Preserve any hand-written notes/columns the user added; never reorder or drop rows.

## Procedure

1. **List the Drive folder once** with sizes:
   `rclone lsf gdrive: --format sp` (size;path), after the preflight above. Build the set of index files
   (regex above) and assign each data part to its batch (nearest timestamp, ≤5 s).
   Ignore everything that isn't a `takeout-*.zip` (e.g. `CATALOG.md`).
2. **Pull `CATALOG.md`** (above). Parse the existing rows.
3. **For each index file already in the catalog and still listed:** skip (just
   refresh `Parts`/`Total size` if they changed, and `Last checked`).
4. **For each new index file:**
   ```bash
   D=$SCRATCH/<timestamp>; mkdir -p "$D"
   rclone copy gdrive:<index-file> "$D"
   unzip -l "$D/<index-file>"
   ```
   Decide `Content` from the listing: paths under `Takeout/Google Photos/` → Google
   Photos; `Takeout/Fit/` (or `Google Fit`) → Google Fit; anything else → `other`.
   The index zip is tiny and may hold only `Takeout/archive_browser.html`; in that
   case extract it (`unzip -o -d "$D" ... 'Takeout/archive_browser.html'`) and read
   the product names it lists (e.g. `grep -oiE 'google photos|fit' ...`) rather than
   downloading 2 GB data parts. Only download a `-1-001.zip` and `unzip -l` it if
   neither the listing nor `archive_browser.html` answers the question.
   Add the row; delete the scratch dir afterwards (`rm -rf "$D"`).
5. **Deleted batches:** any catalogued index file that is no longer in the Drive
   listing → set `Status` to `deleted`. **Never remove the row.** If a `deleted` file
   reappears, set it back to `present`.
6. **Push `CATALOG.md`** back to `gdrive:CATALOG.md`, then print a summary:
   new batches added, marked deleted, unchanged, and the current table.

## Rules

- Only the small `-001.zip` index files are downloaded by default. Never pull the
  multi-GB parts unless step 4 says it's unavoidable.
- Don't unzip data parts or touch the track_me database; this skill only catalogs.
- Never delete anything from Drive.
