"""track-me command-line interface (Django-free).

A single entrypoint over the SQLite pipeline. Subcommands are added as each
piece is rewired off Django:

    track-me ingest <takeout-dir> [--force] [--thumbnails]
    track-me serve [--port 5000]        # launch the timeline map viewer

(`geocode` / `export` / `timeline` land as Phase 3/4 rewires them.)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from track_me import config


def _cmd_ingest(args: argparse.Namespace) -> None:
    from library.ingest.pipeline import IngestPipeline

    config.ensure_dirs()
    print(f"Ingesting from: {args.directory}")
    if args.force:
        print("Force reprocess enabled")
    if args.thumbnails:
        print("Thumbnails enabled")

    pipeline = IngestPipeline(progress_callback=print, generate_thumbnails=args.thumbnails)
    stats = pipeline.ingest_directory(args.directory, force=args.force, limit=args.limit)

    print("\n" + "=" * 60)
    print(f"Total media files: {stats.total_files}")
    print(f"Created:  {stats.created}")
    print(f"Updated:  {stats.updated}")
    print(f"Skipped:  {stats.skipped}")
    print(f"Sidecars matched: {stats.with_sidecar}")
    print(f"Located: {stats.with_location}   No location: {stats.without_location}")
    if stats.errors:
        print(f"Errors: {stats.errors}")
        for detail in stats.error_details[:10]:
            print(f"  {detail}", file=sys.stderr)
    print("=" * 60)


def _cmd_geocode(args: argparse.Namespace) -> None:
    from places.geocode import DEFAULT_GEOCODE_RESOLUTION, Geocoder, estimate_calls
    from track_me.db import Database

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
    from library.export import located_items, media_to_geojson, media_to_gpx
    from track_me.db import Database

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


def _cmd_serve(args: argparse.Namespace) -> None:
    # Run the Flask viewer via its own module so its key/env handling is reused.
    app_path = Path(__file__).resolve().parent.parent / "viewer" / "app.py"
    env = {**os.environ, "VIEWER_PORT": str(args.port)}
    subprocess.run([sys.executable, str(app_path)], env=env, check=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="track-me", description="Local photo travel timeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="Ingest a Google Takeout extract")
    p_ingest.add_argument("directory", help="Takeout extract directory to ingest")
    p_ingest.add_argument("--force", action="store_true", help="Reprocess already-ingested items")
    p_ingest.add_argument("--thumbnails", action="store_true", help="Also generate thumbnails")
    p_ingest.add_argument(
        "--limit", type=int, help="Process at most N files (subset, e.g. for a quick test run)"
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

    p_serve = sub.add_parser("serve", help="Launch the timeline map viewer")
    p_serve.add_argument("--port", type=int, default=5000, help="port (default: 5000)")
    p_serve.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
