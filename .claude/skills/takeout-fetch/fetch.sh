#!/usr/bin/env bash
# Download every zip of one catalogued Takeout batch from gdrive: and unzip it.
#
# usage: fetch.sh <batch> [--mode auto|all|per-part] [--zips DIR] [--out DIR]
#                         [--delete-zips] [--dry-run]
#   <batch>  index timestamp (20260820T042324Z) or file (takeout-20260820T042324Z-001.zip)
#
# Read-only on gdrive: (lsf / cat / copy only). Re-runnable: rclone skips zips already
# downloaded with the right size, and parts already unzipped are skipped by marker file.
set -euo pipefail

SCRATCH=${SCRATCH:-/mnt/scratch/tmp}            # zips + work files
MODE=auto ZIPS="" OUT="" DELETE_ZIPS=0 DRY_RUN=0 BATCH=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --mode) MODE=$2; shift 2 ;;
        --zips) ZIPS=$2; shift 2 ;;
        --out) OUT=$2; shift 2 ;;
        --delete-zips) DELETE_ZIPS=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -*) echo "unknown option $1" >&2; exit 2 ;;
        *) BATCH=$1; shift ;;
    esac
done
[[ $MODE =~ ^(auto|all|per-part)$ ]] || { echo "bad --mode $MODE" >&2; exit 2; }
TS=$(grep -oE '[0-9]{8}T[0-9]{6}Z' <<<"$BATCH" | head -1) || true
[[ -n $TS ]] || { echo "usage: fetch.sh <batch-timestamp|index-file> [...]" >&2; exit 2; }
INDEX="takeout-$TS-001.zip"
ZIPS=${ZIPS:-$SCRATCH/zips/$TS}
OUT=${OUT:-/mnt/scratch/takeout/$TS}
WORK=$SCRATCH/.fetch-$TS
case $SCRATCH$ZIPS$OUT in
    */mnt/scratch*|/mnt/scratch*)
        mountpoint -q /mnt/scratch \
            || { echo "ABORT: /mnt/scratch is not mounted; run: vm-tracker add scratch" >&2; exit 1; } ;;
esac
mkdir -p "$WORK"
trap 'rm -rf "$WORK"' EXIT  # listing/catalog/index copies; re-fetched on every run
log() { echo "[$(date +%H:%M:%S)] $*"; }
die() { echo "ABORT: $*" >&2; exit 1; }

# 1. Preflight: the remote root must be the Takeout folder.
rclone lsf gdrive: --format sp >"$WORK/listing" || die "rclone lsf gdrive: failed"
grep -qE ';takeout-[0-9]{8}T[0-9]{6}Z-([0-9]+-)?[0-9]{3}\.zip$' "$WORK/listing" \
    || { cat "$WORK/listing"; die "no takeout zips in gdrive: root (check root_folder_id)"; }

# 2. The batch must be catalogued, present and complete.
rclone copyto gdrive:CATALOG.md "$WORK/CATALOG.md" \
    || die "no gdrive:CATALOG.md; run the takeout-catalog skill first"
python3 -I - "$WORK/CATALOG.md" "$WORK/listing" "$INDEX" >"$WORK/parts" <<'EOF'
import datetime as dt, re, sys
catalog, listing, index = sys.argv[1:]
rows = [[c.strip() for c in l.strip().strip("|").split("|")]
        for l in open(catalog, encoding="utf-8") if l.startswith("|")]
head = rows[0]
row = next((dict(zip(head, r)) for r in rows[2:] if r and r[0] == index), None)
if row is None:
    sys.exit(f"ABORT: {index} is not in CATALOG.md; run the takeout-catalog skill first")
if row.get("Status") != "present":
    sys.exit(f"ABORT: {index} has Status {row.get('Status')!r} in CATALOG.md")
if row.get("Complete") != "yes":
    sys.exit(f"ABORT: {index} has Complete {row.get('Complete')!r} in CATALOG.md")
ts = lambda s: dt.datetime.strptime(s, "%Y%m%dT%H%M%SZ")
files = [l.rstrip("\n").split(";", 1) for l in open(listing)]
indexes = {m[1]: ts(m[1]) for _, p in files
           if (m := re.match(r"^takeout-(\d{8}T\d{6}Z)-001\.zip$", p))}
mine = ts(re.search(r"\d{8}T\d{6}Z", index)[0])
if index not in [f"takeout-{k}-001.zip" for k in indexes]:
    sys.exit(f"ABORT: {index} is catalogued but not on Drive; rerun takeout-catalog")
parts = []
for size, p in files:
    m = re.match(r"^takeout-(\d{8}T\d{6}Z)-\d+-(\d{3})\.zip$", p)
    if not m:
        continue
    t = ts(m[1])
    nearest = min(indexes.values(), key=lambda i: abs((t - i).total_seconds()))
    if nearest == mine and abs((t - mine).total_seconds()) <= 5:
        parts.append((int(m[2]), int(size), p))
parts.sort()
# Incomplete on Drive right now (the catalog may be stale) -> abort, never fetch.
if not parts:
    sys.exit("ABORT: no data parts on Drive for this batch")
missing = sorted(set(range(1, parts[-1][0] + 1)) - {n for n, _, _ in parts})
if missing:
    sys.exit(f"ABORT: incomplete, missing parts {missing} on Drive")
if any(s == 0 for _, s, _ in parts):
    sys.exit("ABORT: incomplete, some parts on Drive are 0 bytes")
if parts[-1][1] >= 2_000_000_000:
    sys.exit("ABORT: last part is full-size, so later parts may be missing")
if str(len(parts)) != row.get("Parts"):
    sys.exit(f"ABORT: Drive has {len(parts)} parts, catalog says {row.get('Parts')}; "
             "rerun takeout-catalog")
for _, size, p in parts:
    print(f"{size};{p}")
EOF
NPARTS=$(wc -l <"$WORK/parts")
ZIP_BYTES=$(awk -F';' '{s+=$1} END {printf "%.0f", s}' "$WORK/parts")
MAX_PART=$(awk -F';' '$1>m {m=$1} END {printf "%.0f", m}' "$WORK/parts")
log "$INDEX: $NPARTS parts, $((ZIP_BYTES / 1000000000)) GB of zips"

# 3. Index zip -> expected file count and uncompressed size.
rclone copy "gdrive:$INDEX" "$WORK"
unzip -oq -d "$WORK" "$WORK/$INDEX" 'Takeout/archive_browser.html'
INDEX_INFO=$(python3 -I - "$WORK/Takeout/archive_browser.html" <<'EOF'
import html, re, sys
text = html.unescape(re.sub(r"<[^>]+>", "\n", open(sys.argv[1], encoding="utf-8").read()))
mult = {"KB": 2**10, "MB": 2**20, "GB": 2**30, "TB": 2**40}
found = re.findall(r"^\s*(\d+) files, ([\d.]+) ([KMGT]B)\s*$", text, re.M)
files = sum(int(n) for n, _, _ in found)
if files == 0:
    sys.exit("no 'N files, X GB' lines found in archive_browser.html")
print(files, int(sum(float(v) * mult[u] for _, v, u in found)))
EOF
) || die "cannot read the file count / size from the index zip's archive_browser.html"
read -r EXPECT_FILES UNZ_BYTES <<<"$INDEX_INFO"
((EXPECT_FILES > 0 && UNZ_BYTES > 0)) || die "index reports $EXPECT_FILES files, $UNZ_BYTES bytes"
log "index says $EXPECT_FILES files, $((UNZ_BYTES / 1000000000)) GB uncompressed"

# 4. Disk space -> mode. Count what is already on disk from an earlier run.
have() { [[ -d $1 ]] && du -sb "$1" | cut -f1 || echo 0; }
HAVE_ZIPS=$(have "$ZIPS")
HAVE_OUT=$(have "$OUT")
AVAIL=$(df -B1 --output=avail "$SCRATCH" | tail -1)
SLACK=$((2 * 2**30))
NEED_ALL=$((ZIP_BYTES - HAVE_ZIPS + UNZ_BYTES - HAVE_OUT + SLACK))
NEED_PART=$((MAX_PART + UNZ_BYTES - HAVE_OUT + SLACK))
gb() { echo "$(($1 / 1000000000)) GB"; }
log "free $(gb "$AVAIL"); need $(gb "$NEED_ALL") (all) or $(gb "$NEED_PART") (per-part)"
if [[ $MODE == auto ]]; then
    if ((AVAIL >= NEED_ALL)); then MODE=all
    elif ((AVAIL >= NEED_PART)); then MODE=per-part
    else die "not enough space even per-part: free $(gb "$AVAIL"), need $(gb "$NEED_PART")"
    fi
elif [[ $MODE == all ]] && ((AVAIL < NEED_ALL)); then
    die "--mode all needs $(gb "$NEED_ALL"), free $(gb "$AVAIL"); try --mode per-part"
elif [[ $MODE == per-part ]] && ((AVAIL < NEED_PART)); then
    die "--mode per-part needs $(gb "$NEED_PART"), free $(gb "$AVAIL")"
fi
log "mode $MODE; zips -> $ZIPS; extract -> $OUT"
((DRY_RUN)) && { log "dry run: stopping before download"; exit 0; }
mkdir -p "$ZIPS" "$OUT/.fetch-done"

check_size() {  # <local file> <expected bytes>
    [[ -f $1 && $(stat -c %s "$1") == "$2" ]] || die "$1 size mismatch (want $2)"
}
extract() {  # <zip file>
    local name done_marker
    name=$(basename "$1")
    done_marker=$OUT/.fetch-done/$name
    [[ -e $done_marker ]] && { log "skip $name (already unzipped)"; return; }
    log "unzip $name"
    unzip -q -o -d "$OUT" "$1" </dev/null
    touch "$done_marker"
}

# 5. Download + unzip.
cp "$WORK/$INDEX" "$ZIPS/"
extract "$ZIPS/$INDEX"
if [[ $MODE == all ]]; then
    cut -d';' -f2 "$WORK/parts" >"$WORK/files-from"
    log "downloading $NPARTS parts"
    rclone copy gdrive: "$ZIPS" --files-from "$WORK/files-from" \
        --transfers 4 --stats 60s --stats-one-line -v </dev/null
    while IFS=';' read -r size p; do check_size "$ZIPS/$p" "$size"; done <"$WORK/parts"
    log "all parts downloaded and sizes match"
    while IFS=';' read -r size p; do extract "$ZIPS/$p"; done <"$WORK/parts"
    ((DELETE_ZIPS)) && { log "deleting local zips"; rm -f "$ZIPS"/*.zip; }
else
    while IFS=';' read -r size p; do
        if [[ -e $OUT/.fetch-done/$p ]]; then log "skip $p (already unzipped)"; continue; fi
        log "download $p"
        rclone copyto "gdrive:$p" "$ZIPS/$p" </dev/null
        check_size "$ZIPS/$p" "$size"
        extract "$ZIPS/$p"
        rm -f "$ZIPS/$p"  # per-part mode: local zip goes once unzipped (never on Drive)
    done <"$WORK/parts"
fi

# 6. Verify against the index's file count (catches anything the size checks missed).
GOT=$(find "$OUT/Takeout" -type f ! -path "$OUT/Takeout/archive_browser.html" | wc -l)
log "extracted $GOT files; index expects $EXPECT_FILES"
[[ $GOT == "$EXPECT_FILES" ]] || die "file count mismatch ($GOT vs $EXPECT_FILES)"
log "done: $OUT/Takeout ($(du -sh "$OUT" | cut -f1))"
