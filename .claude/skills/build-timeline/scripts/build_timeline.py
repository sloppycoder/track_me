#!/usr/bin/env python
"""Draft or write a travel timeline. Default path for the common case; for
per-stay refinements (merge/relabel/split) import timeline_lib directly.

    # Preview only (does NOT write — show this to the user first):
    python build_timeline.py --start 2019-01-01 --end 2020-01-01 --level country

    # City-level, one region, custom metro merge radius:
    python build_timeline.py --start 2023-06-01 --end 2023-07-01 \
        --level city --region ES FR --merge-km 40

    # Persist ONLY after the user confirms the preview:
    python build_timeline.py --start 2019-01-01 --end 2020-01-01 --level country \
        --write --id countries-2019 --title "Countries visited in 2019" \
        --prompt "build a country-level timeline of everywhere I went in 2019"
"""

from __future__ import annotations

import argparse

import timeline_lib as tl


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True, help="ISO date, inclusive (UTC)")
    ap.add_argument("--end", required=True, help="ISO date, exclusive (UTC)")
    ap.add_argument("--level", choices=["country", "city"], default="country")
    ap.add_argument("--region", nargs="*", help="restrict to these ISO country codes")
    ap.add_argument("--merge-km", type=float, default=50.0, help="city metro merge radius")
    ap.add_argument("--min-hours", type=int, default=24, help="blip-smoothing threshold")
    ap.add_argument("--write", action="store_true", help="persist (only after user confirms)")
    ap.add_argument("--id", help="timeline id / filename stem (required with --write)")
    ap.add_argument("--title", help="human title (required with --write)")
    ap.add_argument("--prompt", action="append", default=[], help="repeatable; the prompt trail")
    args = ap.parse_args()

    points = tl.load_points(args.start, args.end, region=args.region)
    if args.level == "country":
        stays = tl.country_stays(points, min_hours=args.min_hours)
    else:
        stays = tl.city_stays(points, merge_km=args.merge_km, min_hours=args.min_hours)

    print(tl.preview(stays))

    if not args.write:
        print("\n(preview only — re-run with --write --id --title once the user confirms)")
        return

    if not args.id or not args.title:
        ap.error("--write requires --id and --title")
    doc = tl.to_document(stays, timeline_id=args.id, title=args.title, prompts=args.prompt)
    out = tl.write_timeline(doc)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
