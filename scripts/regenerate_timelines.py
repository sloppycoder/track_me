#!/usr/bin/env python3
"""One-off offline patch: regenerate every userdata/timelines/<id>.json in place.

Rebuilds each timeline from the catalog using the `build` params already stored
in its JSON, so the baked `points` payload picks up any change to the label logic
(e.g. the metro-aware city_label). Purely offline — reads the local SQLite DB,
makes NO geocoding API calls. Safe to re-run.

    uv run python scripts/regenerate_timelines.py          # rewrite all
    uv run python scripts/regenerate_timelines.py --dry-run # show, don't write

This deliberately lives outside the CLI (it is a migration helper, not a command).
"""

from __future__ import annotations

import argparse
import json
import sys

from track_me import config
from track_me.timeline import build_stays, load_points, to_document, write_timeline


def regenerate(path, *, dry_run: bool) -> str:
    doc = json.loads(path.read_text())
    build = doc.get("build")
    if not build:
        return f"skip {path.name}: no build params stored"

    points = load_points(build["start"], build["end"], region=build.get("region"))
    stays = build_stays(
        build["start"],
        build["end"],
        level=build.get("level", "country"),
        region=build.get("region"),
        merge_km=build.get("merge_km", 50.0),
        min_hours=build.get("min_hours", 24),
        points=points,
    )
    new_doc = to_document(
        stays,
        timeline_id=doc["id"],
        title=doc.get("title", doc["id"]),
        prompts=doc.get("prompts", []),
        points=points,
        build=build,
    )
    if dry_run:
        return f"[dry-run] {path.name}: {len(stays)} stays, {len(points)} points"
    write_timeline(new_doc)
    return f"wrote {path.name}: {len(stays)} stays, {len(points)} points"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="show what would change")
    args = ap.parse_args()

    config.ensure_dirs()
    files = sorted(config.TIMELINES_DIR.glob("*.json"))
    if not files:
        print(f"no timelines under {config.TIMELINES_DIR}")
        return 1
    for path in files:
        print(regenerate(path, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
