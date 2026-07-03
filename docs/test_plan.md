# Test plan — ingest + geocode (real data)

Validate the Django-free `ingest` and `geocode` steps against real Takeout data by
building a small, dated slice of the catalog and **comparing it row-for-row with the
legacy Django database** (`userdata/track_me_legacy.db`), which is a **superset** of
the test set. Scope is deliberately limited to **ingest and geocode** — no timeline.

The comparison is meaningful because `dedupe_key` is computed identically in the old
and new pipelines, so the same photo has the same key in both DBs and rows line up
1:1.

## Prerequisites

- Run on the machine that has the Takeout library. Set `export TAKEOUT=/path/to/extract`.
- `.env` has `GOOGLE_MAPS_API_KEY` (needed for `geocode`).
- `uv sync` has been run so the `track-me` CLI is available.
- `userdata/track_me_legacy.db` (the old Django DB) is present — this is the baseline.
  The new `userdata/track_me.db` is created fresh on the first `ingest`.

## Test set (three single-month slices)

| Slice | `--filter` | ~photos | ~GPS | ~no-GPS | Exercises |
|---|---|---|---|---|---|
| Early, GPS-rich  | `2012-03` | 101 | 96  | 5  | ingest + geocode of older photos |
| Recent, GPS-rich | `2023-06` | 139 | 137 | 2  | ingest + geocode of recent photos |
| No-GPS           | `2004-02` | 72  | 0   | 72 | ingest of undated-location photos |

(Counts are from the legacy DB as a proxy; the actual re-ingest may differ by a few —
that difference is exactly what this plan surfaces.)

## Step 1 — Ingest

```bash
track-me ingest "$TAKEOUT" --filter 2012-03
track-me ingest "$TAKEOUT" --filter 2023-06
track-me ingest "$TAKEOUT" --filter 2004-02
```

Sanity checks (expected):
- Each run prints `Created` ≈ the table above and `Filtered (outside month range)` > 0.
- Total rows ≈ 312: `sqlite3 userdata/track_me.db "select count(*) from media"`.
- Every row has a `taken_at` and a `local_date`.
- The `2004-02` rows have `latitude IS NULL`, `needs_review = 1`, `geo_cell IS NULL`.
- Re-running any slice a second time creates 0 and skips the rest (incremental).

## Step 2 — Geocode

```bash
track-me geocode --estimate      # preview API-call count (should be small, < free tier)
track-me geocode                 # fetch (Google) + derive city/admin1
```

Sanity checks (expected):
- `place` has one row per distinct res-9 cell; each has `country_code`,
  `formatted_address`, `geocode_raw`, and a derived `city`.
- Every **located** photo now has `geo_cell` set; no-GPS photos still have `geo_cell IS NULL`.
- `track-me geocode` a second time makes **0 API calls** (cells already fetched).
- `track-me geocode --derive-only` re-derives `city`/`admin1` with 0 API calls.

## Step 3 — Compare against the legacy DB (the key step)

**After ingest + geocode, compare the new DB with `userdata/track_me_legacy.db` for the
same photos and highlight every difference.** Join on `dedupe_key` (legacy is a superset,
so every new key should exist in legacy). Account for the deliberate schema changes:

- `taken_at` is stored ISO-8601 in the new DB (`...T...+00:00`) vs Django's
  space-separated UTC in legacy — **parse both to instants** before comparing.
- Column renamed: legacy `time_source` ↔ new `taken_at_source`.
- Coordinates are `REAL` now vs Django `Decimal` — compare **rounded to 5 dp**.
- Geocoding: legacy stored `country_code` + `place_label` on the row; the new DB stores
  them on `place` (join via `media.geo_cell`), plus a new `city`. Legacy has **no `city`**.
  `formatted_address`/`place_label` may legitimately differ (live Google results drift,
  language) — treat those as **informational**, but `country_code` should match.

Run this comparison script and review its output:

```bash
uv run python - <<'PY'
import sqlite3
from datetime import datetime, timezone

new = sqlite3.connect("userdata/track_me.db");        new.row_factory = sqlite3.Row
old = sqlite3.connect("userdata/track_me_legacy.db"); old.row_factory = sqlite3.Row

def norm_dt(s):
    if not s:
        return None
    d = datetime.fromisoformat(s.replace(" ", "T"))
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)

def rnd(x):
    return None if x is None else round(float(x), 5)

new_rows = {r["dedupe_key"]: r for r in new.execute("""
    SELECT m.dedupe_key, m.taken_at, m.latitude, m.longitude, m.timezone,
           m.location_source, m.taken_at_source, m.file_name, m.google_photos_url,
           m.needs_review, p.country_code AS country_code, p.city AS city,
           p.formatted_address AS formatted_address
    FROM media m LEFT JOIN place p ON m.geo_cell = p.h3_cell
""")}
keys = list(new_rows)
qmarks = ",".join("?" * len(keys))
old_rows = {r["dedupe_key"]: r for r in old.execute(f"""
    SELECT dedupe_key, taken_at, latitude, longitude, timezone, location_source,
           time_source, file_name, google_photos_url, needs_review,
           country_code, place_label
    FROM media_item WHERE dedupe_key IN ({qmarks})
""", keys)}

print(f"new photos: {len(keys)}   matched in legacy: {len(old_rows)}")
only_new = [k for k in keys if k not in old_rows]
print(f"only in NEW (not in legacy superset): {len(only_new)}")
for k in only_new[:10]:
    print("   ", k, new_rows[k]["file_name"])

# ingest-field comparison (should match)
fields = [
    ("taken_at",        lambda n, o: norm_dt(n["taken_at"]) == norm_dt(o["taken_at"])),
    ("latitude",        lambda n, o: rnd(n["latitude"])  == rnd(o["latitude"])),
    ("longitude",       lambda n, o: rnd(n["longitude"]) == rnd(o["longitude"])),
    ("timezone",        lambda n, o: n["timezone"] == o["timezone"]),
    ("location_source", lambda n, o: n["location_source"] == o["location_source"]),
    ("taken_at_source", lambda n, o: n["taken_at_source"] == o["time_source"]),
    ("file_name",       lambda n, o: n["file_name"] == o["file_name"]),
    ("google_url",      lambda n, o: n["google_photos_url"] == o["google_photos_url"]),
    ("needs_review",    lambda n, o: bool(n["needs_review"]) == bool(o["needs_review"])),
    # geocode: country should match; place text is informational
    ("country_code",    lambda n, o: (n["country_code"] or None) == (o["country_code"] or None)),
]
print("\n=== ingest/geocode field mismatches (same photos) ===")
for name, eq in fields:
    bad = [k for k in keys if k in old_rows and not eq(new_rows[k], old_rows[k])]
    print(f"  {name:<16} {len(bad):>4} mismatched")
    for k in bad[:3]:
        n, o = new_rows[k], old_rows[k]
        col = {"taken_at_source": "time_source", "google_url": "google_photos_url",
               "country_code": "country_code"}.get(name, name)
        nv = n["taken_at_source"] if name == "taken_at_source" else \
             n["google_photos_url"] if name == "google_url" else n[col]
        print(f"      {k[:12]} {n['file_name']:<22} new={nv!r:<22} old={o[col]!r}")

# informational: place-text drift + new-only city coverage
print("\n=== informational ===")
loc = [k for k in keys if new_rows[k]["latitude"] is not None]
withcity = [k for k in loc if new_rows[k]["city"]]
print(f"  located photos: {len(loc)}   with derived city: {len(withcity)}")
addr_diff = [k for k in keys if k in old_rows and new_rows[k]["formatted_address"]
             and (new_rows[k]["formatted_address"] != old_rows[k]["place_label"])]
print(f"  formatted_address differs from legacy place_label: {len(addr_diff)} (expected; Google drift)")
PY
```

## Pass / fail

**Pass** when, over the same photos:
- `only in NEW` is **0** (every new key exists in the legacy superset).
- `taken_at`, `latitude`, `longitude`, `timezone`, `location_source`,
  `taken_at_source`, `file_name`, `google_url`, `needs_review` mismatches are **0**.
- `country_code` mismatches are **0** (or each explained by live-geocode drift).

**Investigate** any non-zero ingest-field mismatch — that's a rewrite bug. Differences in
`formatted_address` vs `place_label`, and the new `city` column, are expected and
informational (city has no legacy counterpart; Google's text can change between runs).

Keep `userdata/track_me_legacy.db` until the comparison passes; then it can be removed.
