from django.core.management.base import BaseCommand

from myphoto.services.geocoding_service import GeocodingService


class Command(BaseCommand):
    help = "Geocode photos using Google Maps API (grouped by H3 spatial index)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--h3-resolution",
            type=int,
            default=9,
            help="H3 resolution for grouping (9=~11km², 12=~0.3km²) - default: 9",
        )
        parser.add_argument(
            "--recalculate",
            action="store_true",
            help="Recalculate geocoding even if already done",
        )
        parser.add_argument(
            "--api-key",
            type=str,
            help="Google Maps API key (or use GOOGLE_MAPS_API_KEY in settings)",
        )

    def handle(self, *args, **options):
        h3_resolution = options["h3_resolution"]
        recalculate = options["recalculate"]
        api_key = options["api_key"]

        self.stdout.write(f"Geocoding photos at H3 resolution {h3_resolution}")
        if recalculate:
            self.stdout.write(self.style.WARNING("Recalculate mode enabled"))

        try:
            # Create service
            service = GeocodingService(
                google_api_key=api_key, progress_callback=self.stdout.write
            )

            # Geocode photos
            stats = service.geocode_photos(
                h3_resolution=h3_resolution,
                recalculate=recalculate,
            )

            # Display results
            self.stdout.write("\n" + "=" * 60)
            self.stdout.write(f"Total photos: {stats['total_photos']}")

            if stats["processed_photos"] > 0:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Geocoded {stats['processed_photos']} photos "
                        f"({stats['api_calls']} API calls)"
                    )
                )
            if stats["skipped_photos"] > 0:
                self.stdout.write(self.style.WARNING(f"Skipped {stats['skipped_photos']} photos"))
            if stats["errors"] > 0:
                self.stdout.write(self.style.ERROR(f"Errors: {stats['errors']}"))
                for error in stats["error_details"][:5]:
                    self.stderr.write(f"  {error}")

            self.stdout.write("=" * 60)

        except ValueError as e:
            self.stderr.write(self.style.ERROR(str(e)))
        except ImportError as e:
            self.stderr.write(self.style.ERROR(str(e)))
            self.stderr.write("Install required package: pip install googlemaps pytz")
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Geocoding failed: {e}"))
