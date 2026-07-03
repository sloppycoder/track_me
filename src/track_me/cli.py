"""track-me command-line interface (Django-free).

A single entrypoint over the local SQLite pipeline:

    track-me ingest <takeout-dir> [--force] [--thumbnails] [--limit N]
    track-me geocode [--resolution 9] [--recalculate] [--estimate] [--derive-only]
    track-me export [--format gpx|geojson] [--output FILE] [--year Y]
    track-me timeline --start ISO --end ISO [--level country|city] [--write ...]
    track-me serve [--port 5000]        # launch the timeline map viewer
"""

from __future__ import annotations

import argparse
import io
import os
import re
import subprocess
import sys
from pathlib import Path

from track_me import config


def _parse_filter(raw: str | None) -> tuple[str, str] | None:
    """Parse '--filter YYYY-MM[,YYYY-MM]' into an inclusive (lo, hi) month range."""
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",")]
    lo, hi = parts[0], parts[-1]  # single value -> that month only
    for ym in (lo, hi):
        if not re.fullmatch(r"\d{4}-\d{2}", ym):
            raise SystemExit(f"--filter expects YYYY-MM[,YYYY-MM]; got '{raw}'")
    return (min(lo, hi), max(lo, hi))


def _cmd_ingest(args: argparse.Namespace) -> None:
    from track_me.ingest.pipeline import IngestPipeline

    date_filter = _parse_filter(args.filter)
    config.ensure_dirs()
    print(f"Ingesting from: {args.source}")
    if args.force:
        print("Force reprocess enabled")
    if args.thumbnails:
        print("Thumbnails enabled")
    if date_filter:
        print(f"Capture-month filter: {date_filter[0]}..{date_filter[1]}")

    pipeline = IngestPipeline(progress_callback=print, generate_thumbnails=args.thumbnails)
    stats = pipeline.ingest(
        args.source, force=args.force, date_filter=date_filter, workers=args.workers
    )

    print("\n" + "=" * 60)
    print(f"Total media files: {stats.total_files}")
    print(f"Created:   {stats.created}")
    print(f"Updated:   {stats.updated}")
    print(f"Refreshed: {stats.refreshed}")
    print(f"Skipped:   {stats.skipped}")
    if stats.filtered:
        print(f"Filtered (outside month range): {stats.filtered}")
    print(f"Sidecars matched: {stats.with_sidecar}")
    print(f"Located: {stats.with_location}   No location: {stats.without_location}")
    if stats.errors:
        print(f"Errors: {stats.errors}")
        for detail in stats.error_details[:10]:
            print(f"  {detail}", file=sys.stderr)
    print("=" * 60)


def _cmd_geocode(args: argparse.Namespace) -> None:
    from track_me.db import Database
    from track_me.geocode import DEFAULT_GEOCODE_RESOLUTION, Geocoder, estimate_calls

    db = Database(config.DB_PATH)
    db.init_schema()
    free_tier = 10_000

    if args.estimate:
        resolutions = sorted({6, 9, 10, 11, args.resolution})
        total, counts = estimate_calls(db, resolutions, args.recalculate)
        print(f"Located items pending geocoding: {total}")
        print("Distinct H3 cells = Google API calls needed (no calls made):")
        for r in resolutions:
            n = counts[r]
            fits = "fits free tier" if n <= free_tier else f"EXCEEDS {free_tier}/mo"
            tag = "  (default)" if r == DEFAULT_GEOCODE_RESOLUTION else ""
            print(f"  res {r:>2}: {n:>6} calls  [{fits}]{tag}")
        return

    geocoder = Geocoder(db=db, api_key=args.api_key, progress_callback=print)
    if args.derive_only:
        n = geocoder.derive_all(redo=True)
        print(f"Re-derived city/admin1 for {n} places (no API calls)")
        return

    try:
        stats = geocoder.geocode_items(
            resolution=args.resolution,
            recalculate=args.recalculate,
            max_api_calls=args.max_api_calls,
        )
    except ValueError as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(1) from e

    print("\n" + "=" * 60)
    print(f"Total located items pending: {stats.total_items}")
    print(f"Geocoded {stats.processed} items ({stats.api_calls} API calls)")
    if stats.skipped:
        print(f"Skipped (no result): {stats.skipped}")
    if stats.errors:
        print(f"Errors: {stats.errors}")
    print("=" * 60)


def _cmd_export(args: argparse.Namespace) -> None:
    from track_me.db import Database
    from track_me.export import located_items, media_to_geojson, media_to_gpx

    db = Database(config.DB_PATH)
    db.init_schema()
    items = located_items(db, year=args.year)
    text = media_to_geojson(items) if args.format == "geojson" else media_to_gpx(items)

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        span = ""
        if items and items[0].taken_at and items[-1].taken_at:
            span = f" ({items[0].taken_at.date()} … {items[-1].taken_at.date()})"
        print(f"Wrote {len(items)} points to {args.output}{span}", file=sys.stderr)
    else:
        print(text)


def _cmd_timeline(args: argparse.Namespace) -> None:
    from track_me import timeline as tl
    from track_me.db import Database

    db = Database(config.DB_PATH)
    db.init_schema()
    stays = tl.build_stays(
        args.start,
        args.end,
        level=args.level,
        region=args.region,
        merge_km=args.merge_km,
        min_hours=args.min_hours,
        db=db,
    )
    print(tl.preview(stays))

    if not args.write:
        print("\n(preview only — re-run with --write --id --title once the user confirms)")
        return
    if not args.id or not args.title:
        print("--write requires --id and --title", file=sys.stderr)
        raise SystemExit(1)
    doc = tl.to_document(stays, timeline_id=args.id, title=args.title, prompts=args.prompt or [])
    out = tl.write_timeline(doc)
    print(f"\nwrote {out}")


def _cmd_serve(args: argparse.Namespace) -> None:
    # Run the Flask viewer as a module so package imports resolve cleanly.
    env = {**os.environ, "VIEWER_PORT": str(args.port)}
    subprocess.run([sys.executable, "-m", "track_me.viewer.app"], env=env, check=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="track-me", description="Local photo travel timeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="Ingest a Google Takeout extract")
    p_ingest.add_argument(
        "source", help="Takeout source: a local directory or an s3://bucket/prefix URI"
    )
    p_ingest.add_argument("--force", action="store_true", help="Reprocess already-ingested items")
    p_ingest.add_argument("--thumbnails", action="store_true", help="Also generate thumbnails")
    p_ingest.add_argument(
        "--filter",
        metavar="YYYY-MM[,YYYY-MM]",
        help="Only ingest photos taken in this capture-month range (for test runs)",
    )
    p_ingest.add_argument(
        "--workers", type=int, default=32, help="Concurrent read workers (default: 32)"
    )
    p_ingest.set_defaults(func=_cmd_ingest)

    p_geocode = sub.add_parser("geocode", help="Reverse-geocode located media into place names")
    p_geocode.add_argument("--resolution", type=int, default=9, help="H3 batching resolution")
    p_geocode.add_argument("--recalculate", action="store_true", help="Re-geocode existing cells")
    p_geocode.add_argument("--max-api-calls", type=int, help="Cap the number of API calls")
    p_geocode.add_argument("--api-key", help="Google Maps API key override")
    p_geocode.add_argument(
        "--estimate", action="store_true", help="Show API calls per resolution; make no calls"
    )
    p_geocode.add_argument(
        "--derive-only",
        action="store_true",
        help="Recompute city/admin1 from stored responses (offline, no API calls)",
    )
    p_geocode.set_defaults(func=_cmd_geocode)

    p_export = sub.add_parser("export", help="Export located media as GPX/GeoJSON points")
    p_export.add_argument("--format", choices=["gpx", "geojson"], default="gpx")
    p_export.add_argument("--output", help="output file (default: stdout)")
    p_export.add_argument("--year", type=int, help="filter to a single local year")
    p_export.set_defaults(func=_cmd_export)

    p_tl = sub.add_parser("timeline", help="Build a travel timeline (preview; --write to save)")
    p_tl.add_argument("--start", required=True, help="ISO date, inclusive (UTC)")
    p_tl.add_argument("--end", required=True, help="ISO date, exclusive (UTC)")
    p_tl.add_argument("--level", choices=["country", "city"], default="country")
    p_tl.add_argument("--region", nargs="*", help="restrict to these ISO country codes")
    p_tl.add_argument("--merge-km", type=float, default=50.0, help="city metro merge radius")
    p_tl.add_argument("--min-hours", type=int, default=24, help="blip-smoothing threshold")
    p_tl.add_argument("--write", action="store_true", help="persist (only after user confirms)")
    p_tl.add_argument("--id", help="timeline id / filename stem (required with --write)")
    p_tl.add_argument("--title", help="human title (required with --write)")
    p_tl.add_argument(
        "--prompt", action="append", default=[], help="repeatable; the prompt trail"
    )
    p_tl.set_defaults(func=_cmd_timeline)

    p_serve = sub.add_parser("serve", help="Launch the timeline map viewer")
    p_serve.add_argument("--port", type=int, default=5000, help="port (default: 5000)")
    p_serve.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> None:
    # Line-buffer stdout so progress streams live when piped (e.g. `| tee`),
    # instead of block-buffering until the process exits.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
