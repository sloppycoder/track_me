from pathlib import Path

from django.core.management.base import BaseCommand

from library.export import located_items, media_to_geojson, media_to_gpx


class Command(BaseCommand):
    help = "Export located media as timestamped GPX/GeoJSON points (for GeoPulse/Dawarich)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--format", choices=["gpx", "geojson"], default="gpx", help="output format"
        )
        parser.add_argument("--output", type=str, help="output file (default: stdout)")
        parser.add_argument("--year", type=int, help="filter to a single year")

    def handle(self, *args, **options):
        items = list(located_items(year=options.get("year")))
        if options["format"] == "geojson":
            text = media_to_geojson(items)
        else:
            text = media_to_gpx(items)

        if options.get("output"):
            Path(options["output"]).write_text(text, encoding="utf-8")
            span = ""
            if items:
                span = f" ({items[0].taken_at.date()} … {items[-1].taken_at.date()})"
            self.stderr.write(
                self.style.SUCCESS(f"Wrote {len(items)} points to {options['output']}{span}")
            )
        else:
            self.stdout.write(text)
