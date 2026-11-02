"""track-me command-line interface (Django-free).

A single entrypoint over the local SQLite pipeline:

    track-me ingest <takeout-dir> [--force] [--thumbnails] [--limit N]
    track-me geocode [--resolution 9] [--recalculate] [--estimate] [--derive-only]
    track-me export [--format gpx|geojson] [--output FILE] [--year Y]
    track-me timeline --start ISO --end ISO [--level country|city]
    track-me log [-n N]                      # history of data changes (like git log)
    track-me tag [--db PATH] [--host H] [--port 8081]   # web app to tag old photos by hand
    track-me mcp [--host H] [--port 8080] [--no-auth | --stdio]   # MCP server for chat clients
"""

from __future__ import annotations

import argparse
import io
import os
import re
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


def log_ingest(db, stats) -> int | None:
    """Record an ingest in the change log, but only if it wrote rows."""
    if not (stats.created or stats.updated or stats.refreshed):
        return None
    return db.log_change(
        "ingest",
        f"ingest: {stats.created} new, {stats.refreshed} refreshed, {stats.updated} updated",
        {
            "created": stats.created,
            "refreshed": stats.refreshed,
            "updated": stats.updated,
            "skipped": stats.skipped,
            "errors": stats.errors,
        },
    )


def log_geocode(db, stats, derived: int) -> int | None:
    """Record a geocode in the change log, but only if it changed rows."""
    if not (stats.processed or stats.api_calls or derived):
        return None
    return db.log_change(
        "geocode",
        f"geocode: {stats.processed} photos linked, {stats.api_calls} API calls, "
        f"{derived} places re-derived",
        {"linked": stats.processed, "api_calls": stats.api_calls, "rederived": derived},
    )


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

    from track_me.db import Database

    db = Database(config.DB_PATH)
    if (version := log_ingest(db, stats)) is not None:
        print(f"Data version: {version}")


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
        print(f"Re-derived city/admin1: {n} places changed (no API calls)")
        if n:
            db.log_change(
                "geocode", f"geocode --derive-only: {n} places re-derived", {"rederived": n}
            )
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

    # Re-derive every place's names (offline, no API calls) so changes to the naming
    # rules (priority chain, Chinese city normalization) take effect on every run.
    derived = geocoder.derive_all(redo=True)

    print("\n" + "=" * 60)
    print(f"Total located items pending: {stats.total_items}")
    print(f"Geocoded {stats.processed} items ({stats.api_calls} API calls)")
    if stats.skipped:
        print(f"Skipped (no result): {stats.skipped}")
    if stats.errors:
        print(f"Errors: {stats.errors}")
    print(f"Re-derived city/admin1: {derived} places changed (no API calls)")
    print("=" * 60)
    if (version := log_geocode(db, stats, derived)) is not None:
        print(f"Data version: {version}")


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
    points = tl.load_points(args.start, args.end, db=db, region=args.region)
    stays = tl.build_stays(
        args.start,
        args.end,
        level=args.level,
        region=args.region,
        merge_km=args.merge_km,
        min_hours=args.min_hours,
        db=db,
        points=points,
        travel_only=args.travel_only,
    )
    print(tl.preview(stays))


def _cmd_log(args: argparse.Namespace) -> None:
    from track_me.db import Database

    db = Database(config.DB_PATH)
    db.init_schema()
    rows = db.recent_changes(args.n)
    if not rows:
        print("No changes recorded yet.")
    for r in rows:
        print(f"v{r['id']:<4} {r['at'][:19]}Z  {r['summary']}")


def _cmd_tag(args: argparse.Namespace) -> None:
    from track_me import tagger
    from track_me.db import Database

    db_path = Path(args.db) if args.db else config.DB_PATH
    if not db_path.is_file():
        raise SystemExit(f"No catalog at {db_path} (use --db PATH)")
    Database(db_path).init_schema()  # older catalogs gain change_log
    print(f"Catalog:    {db_path}")
    print(f"Thumbnails: {config.THUMBNAIL_CACHE_DIR}  (set THUMBNAIL_CACHE_DIR to change)")
    print(f"Serving on  http://{args.host}:{args.port}  (no login: keep it on loopback)")
    tagger.run(db_path=db_path, host=args.host, port=args.port)


def _cmd_mcp(args: argparse.Namespace) -> None:
    from track_me import mcp_server

    mcp_server.run(host=args.host, port=args.port, no_auth=args.no_auth, stdio=args.stdio)


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

    p_log = sub.add_parser("log", help="Show the history of data changes (like git log)")
    p_log.add_argument("-n", type=int, default=20, help="How many entries (default: 20)")
    p_log.set_defaults(func=_cmd_log)

    p_tag = sub.add_parser("tag", help="Web app: tag photos that have no location, by hand")
    p_tag.add_argument("--db", help="Catalog path (default: DB_PATH / userdata/track_me.db)")
    p_tag.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    p_tag.add_argument("--port", type=int, default=8081, help="Port (default: 8081)")
    p_tag.set_defaults(func=_cmd_tag)

    p_export = sub.add_parser("export", help="Export located media as GPX/GeoJSON points")
    p_export.add_argument("--format", choices=["gpx", "geojson"], default="gpx")
    p_export.add_argument("--output", help="output file (default: stdout)")
    p_export.add_argument("--year", type=int, help="filter to a single local year")
    p_export.set_defaults(func=_cmd_export)

    p_tl = sub.add_parser("timeline", help="Preview a travel timeline (stays) for a date window")
    p_tl.add_argument("--start", required=True, help="ISO date, inclusive (UTC)")
    p_tl.add_argument("--end", required=True, help="ISO date, exclusive (UTC)")
    p_tl.add_argument("--level", choices=["country", "city"], default="country")
    p_tl.add_argument("--region", nargs="*", help="restrict to these ISO country codes")
    p_tl.add_argument("--merge-km", type=float, default=50.0, help="city metro merge radius")
    p_tl.add_argument("--min-hours", type=int, default=24, help="blip-smoothing threshold")
    p_tl.add_argument(
        "--travel-only", action="store_true", help="hide stays near home (200 mi US, else 200 km)"
    )
    p_tl.set_defaults(func=_cmd_timeline)

    p_mcp = sub.add_parser("mcp", help="Serve read-only catalog tools to MCP chat clients")
    p_mcp.add_argument("--host", default="127.0.0.1", help="bind host (default: 127.0.0.1)")
    p_mcp.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT", "8080")),
        help="port (default: $PORT or 8080)",
    )
    mode = p_mcp.add_mutually_exclusive_group()
    mode.add_argument(
        "--no-auth", action="store_true", help="skip Google OAuth (loopback host only; testing)"
    )
    mode.add_argument("--stdio", action="store_true", help="stdio transport for local clients")
    p_mcp.set_defaults(func=_cmd_mcp)

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
