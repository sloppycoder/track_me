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

    p_serve = sub.add_parser("serve", help="Launch the timeline map viewer")
    p_serve.add_argument("--port", type=int, default=5000, help="port (default: 5000)")
    p_serve.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
