from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from myphoto.models import Photo


class Command(BaseCommand):
    help = "Estimate geocoding API calls needed for different H3 resolutions"

    def add_arguments(self, parser):
        parser.add_argument(
            "--show-distribution",
            action="store_true",
            help="Show distribution of photos per location",
        )

    def handle(self, *args, **options):
        show_distribution = options["show_distribution"]

        # Count photos that need geocoding
        photos_needing_geocoding = Photo.objects.filter(
            Q(gps_latitude__isnull=False, gps_longitude__isnull=False),
            Q(geo_coded_at__isnull=True),
        )
        total_photos = photos_needing_geocoding.count()

        if total_photos == 0:
            self.stdout.write(self.style.WARNING("No photos need geocoding"))
            return

        self.stdout.write(f"\nTotal photos needing geocoding: {total_photos}")
        self.stdout.write("=" * 70)

        # Check different H3 resolutions
        resolutions = [
            (3, "~12,000 km²", "Country level"),
            (6, "~290 km²", "Region level"),
            (9, "~11 km²", "City/neighborhood level"),
            (12, "~0.3 km²", "Street level"),
            (15, "~0.9 m²", "Building level"),
        ]

        for res, area, description in resolutions:
            h3_field = f"h3_res_{res}"

            # Count unique H3 cells
            unique_cells = (
                photos_needing_geocoding.values(h3_field)
                .exclude(**{f"{h3_field}__isnull": True})
                .distinct()
                .count()
            )

            # Calculate cost (Google charges for both Geocoding + Timezone APIs)
            # $5 per 1000 requests for each API
            # Free tier: $200/month = 20,000 requests total (10,000 per API)
            api_calls = unique_cells * 2  # Geocoding + Timezone
            cost = (api_calls / 1000) * 5

            self.stdout.write(f"\nResolution {res} ({area}) - {description}")
            self.stdout.write(f"  Unique locations: {unique_cells}")
            self.stdout.write(f"  API calls: {api_calls} (Geocoding + Timezone)")

            if api_calls <= 20000:
                self.stdout.write(
                    self.style.SUCCESS(f"  Cost: ${cost:.2f} (Within free tier! ✓)")
                )
            else:
                self.stdout.write(self.style.WARNING(f"  Cost: ${cost:.2f} (Exceeds free tier)"))

            # Show distribution if requested
            if show_distribution and unique_cells > 0:
                self._show_distribution(photos_needing_geocoding, h3_field, res)

        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("\nRecommendation:")
        self.stdout.write("  - Resolution 12 gives street-level precision")
        self.stdout.write(
            "  - Free tier: 10,000 API calls/month (covers both Geocoding + Timezone)"
        )
        self.stdout.write("  - Choose the finest resolution that fits your free tier quota")

    def _show_distribution(self, photos_queryset, h3_field, resolution):
        """Show distribution of photos per H3 cell."""
        distribution = (
            photos_queryset.values(h3_field)
            .exclude(**{f"{h3_field}__isnull": True})
            .annotate(photo_count=Count("id"))
            .order_by("-photo_count")[:10]
        )

        if distribution:
            self.stdout.write(f"  Top 10 locations (resolution {resolution}):")
            for item in distribution:
                h3_cell = item[h3_field]
                count = item["photo_count"]
                self.stdout.write(f"    {h3_cell}: {count} photos")
