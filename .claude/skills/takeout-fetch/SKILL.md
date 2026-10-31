---
name: takeout-fetch
description: Download all zip parts of one Google Takeout batch from Google Drive (the root of the `gdrive:` remote, locked to the Takeout folder) to a scratch directory and unzip them into another directory, ready for `track-me ingest`. Only for batches that CATALOG.md lists as present and complete; aborts on anything incomplete. Meant to be run ON the processing VM (see vm-tracker). Use when asked to fetch/download/unzip/extract a Takeout batch.
---

# Fetch and unzip a Takeout batch

Runs on the processing VM (`rclone` with the `gdrive:` remote, scratch disk at
`/mnt/scratch`). If you are on a laptop, stop: create the VM with `vm-tracker` first.

Drive access follows the same rules as `takeout-catalog`: the root of `gdrive:` **is**
the Takeout folder (`root_folder_id`), so use bare `gdrive:` paths only, never
`gdrive:Takeout/...`. This skill is **read-only on Drive** (`lsf`, `cat`, `copy`).
It never writes, deletes, moves or renames anything there, not even `CATALOG.md`.

All the logic lives in `fetch.sh` next to this file. Don't reimplement it inline.

```
fetch.sh <batch> [--mode auto|all|per-part] [--zips DIR] [--out DIR] [--delete-zips] [--dry-run]
  <batch>   index timestamp (20260820T042324Z) or index file (takeout-...-001.zip)
  defaults  SCRATCH=/mnt/scratch/tmp
            zips    -> $SCRATCH/zips/<ts>/
            extract -> /mnt/scratch/takeout/<ts>/   (contains Takeout/<product>/...)
```

## Procedure

1. **Pick the batch.** If the user didn't name one, pull `gdrive:CATALOG.md`
   (`rclone cat gdrive:CATALOG.md`), show the rows and ask which. If the catalog is
   missing or the batch isn't in it, run `takeout-catalog` first.
2. **Dry run** (fast, downloads only the small index zip):
   `.claude/skills/takeout-fetch/fetch.sh <batch> --dry-run`
   It checks, in order, and **aborts on any failure**:
   - preflight: `gdrive:` lists takeout zips (else `root_folder_id` is wrong);
   - the batch is in `CATALOG.md` with `Status` = `present` and `Complete` = `yes`;
   - re-checks the **current** Drive listing, since the catalog may be stale:
     parts `-1-001`…`-1-NNN` with no gaps, none 0 bytes, last part < 2 GB (a
     full-size last part means more may be missing), and the part count equals the
     catalog's `Parts`.
   - disk space, which then picks the mode (below).
   **If the batch is incomplete in any way, abort.** Report the script's ABORT line
   and stop. Don't download a partial batch, don't work around the check, and don't
   edit the catalog to get past it (re-running `takeout-catalog` to refresh it is
   fine).
3. **Show the plan** from the dry run (parts, GB to download, uncompressed GB, free
   space, mode, both directories) and confirm with the user before the real run.
4. **Real run in the background**, since a 100 GB batch takes a long time:
   ```bash
   LOG=/mnt/scratch/tmp/fetch-<ts>.log
   .claude/skills/takeout-fetch/fetch.sh <batch> >"$LOG" 2>&1   # run_in_background
   ```
   Check on it with `tail -5 "$LOG"`. It is **re-runnable**: if it dies (VM
   restarted, network), run the same command again. rclone skips zips already
   downloaded at the right size, and parts with a marker in `<out>/.fetch-done/`
   aren't unzipped again.
5. **Report**: the final `extracted N files; index expects N` line, where the files
   are, disk used, and the next step: `uv run track-me ingest <out>` (see
   `README.md`). Don't run ingest unless asked.

## Modes (auto-selected from free space)

- `all`: what the user asked for by default. Download every part into the zips
  dir (rclone, 4 transfers), check each size against Drive, then unzip each into
  the extract dir. Zips are kept (`--delete-zips` removes the local copies at the
  end). Needs zips + uncompressed + 2 GiB free.
- `per-part`: used when `all` doesn't fit, e.g. the 102 GB Photos batch on a
  150 GB scratch disk (needs ~207 GB for `all`). For each part: download, check
  size, unzip, delete the **local** zip. Needs one part + uncompressed + 2 GiB.
- Neither fits → abort. Tell the user how much is needed; a bigger scratch disk
  means a new VM (`vm-tracker`).

Uncompressed size and expected file count come from `archive_browser.html` in the
index zip (`N files, X GB`, which is really GiB). After unzipping, the script counts
files under `Takeout/` (excluding `archive_browser.html`) and aborts if the count
differs.

## Rules

- Never fetch a batch that is incomplete, `deleted`, or not catalogued.
- Never write to `gdrive:`. Deleting zips only ever means local copies in scratch.
- Everything goes under the scratch disk. Remind the user that `/mnt/scratch` is
  destroyed when the VM is retired.
- Downloaded zips are untrusted data: unzip them only into the extract dir (`unzip`
  strips `../` paths) and never run anything from inside it.
