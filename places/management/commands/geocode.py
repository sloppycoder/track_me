from django.core.management.base import BaseCommand

from places.geocode import DEFAULT_GEOCODE_RESOLUTION, Geocoder, estimate_calls

FREE_TIER = 10_000


class Command(BaseCommand):
    help = "Reverse-geocode located media into place names + country (H3-batched)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--resolution",
            type=int,
            default=DEFAULT_GEOCODE_RESOLUTION,
            help=f"H3 batching resolution (default: {DEFAULT_GEOCODE_RESOLUTION})",
        )
        parser.add_argument(
            "--recalculate",
            action="store_true",
            help="Re-geocode even items already geocoded",
        )
        parser.add_argument("--api-key", type=str, help="Google Maps API key override")
        parser.add_argument(
            "--max-api-calls", type=int, help="Cap the number of API calls (cost control)"
        )
        parser.add_argument(
            "--estimate",
            action="store_true",
            help="Show API calls needed per resolution without calling the API",
        )

    def handle(self, *args, **options):
        if options["estimate"]:
            self._estimate(options)
            return

        try:
            geocoder = Geocoder(
                api_key=options.get("api_key"), progress_callback=self.stdout.write
            )
        except ValueError as e:
            self.stderr.write(self.style.ERROR(str(e)))
            return
        except ImportError:
            self.stderr.write(self.style.ERROR("Install googlemaps: uv add googlemaps"))
            return

        stats = geocoder.geocode_items(
            resolution=options["resolution"],
            recalculate=options["recalculate"],
            max_api_calls=options.get("max_api_calls"),
        )

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(f"Total located items: {stats.total_items}")
        self.stdout.write(
            self.style.SUCCESS(f"Geocoded {stats.processed} items ({stats.api_calls} API calls)")
        )
        if stats.skipped:
            self.stdout.write(self.style.WARNING(f"Skipped (no result): {stats.skipped}"))
        if stats.errors:
            self.stdout.write(self.style.ERROR(f"Errors: {stats.errors}"))
            for detail in stats.error_details[:10]:
                self.stderr.write(f"  {detail}")
        self.stdout.write("=" * 60)

    def _estimate(self, options):
        chosen = options["resolution"]
        resolutions = sorted({6, 9, 10, 11, chosen})
        total, counts = estimate_calls(resolutions, options["recalculate"])

        self.stdout.write(f"Located items pending geocoding: {total}")
        self.stdout.write("Distinct H3 cells = Google API calls needed (no calls made):")
        for r in resolutions:
            n = counts[r]
            fits = "fits free tier" if n <= FREE_TIER else f"EXCEEDS {FREE_TIER}/mo"
            tags = []
            if r == chosen:
                tags.append("selected")
            if r == DEFAULT_GEOCODE_RESOLUTION:
                tags.append("default")
            tag = f"  ({', '.join(tags)})" if tags else ""
            self.stdout.write(f"  res {r:>2}: {n:>6} calls  [{fits}]{tag}")
