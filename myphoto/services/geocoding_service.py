"""
Service for geocoding photos using Google Maps Geocoding API.
Uses H3 spatial indexing to batch geocode photos at similar locations.
"""

import logging
from collections import defaultdict
from datetime import datetime
from typing import Optional

import h3
from django.conf import settings
from django.db.models import Q
from django.utils import timezone as django_timezone

from myphoto.models import Photo

logger = logging.getLogger(__name__)


class GeocodingService:
    """Service to geocode photos and calculate timezone-aware timestamps."""

    def __init__(self, google_api_key: str | None = None, progress_callback=None):
        """
        Initialize the geocoding service.

        Args:
            google_api_key: Google Maps API key (or use GOOGLE_MAPS_API_KEY from settings)
            progress_callback: Optional function(message: str) to report progress
        """
        self.api_key = google_api_key or getattr(settings, "GOOGLE_MAPS_API_KEY", None)
        self.progress_callback = progress_callback or (lambda x: None)

        if not self.api_key:
            raise ValueError(
                "Google Maps API key required. "
                "Set GOOGLE_MAPS_API_KEY in settings or pass google_api_key parameter."
            )

        # Import googlemaps (lazy import to avoid requiring it if not using geocoding)
        import googlemaps

        self.gmaps = googlemaps.Client(key=self.api_key)

    def geocode_photos(
        self, h3_resolution: int = 9, batch_size: int = 100, recalculate: bool = False
    ) -> dict:
        """
        Geocode photos by grouping them using H3 spatial index.

        Photos at similar locations (same H3 cell) share the same geocoding result,
        minimizing API calls.

        Args:
            h3_resolution: H3 resolution for grouping (9 = ~11km², 12 = ~0.3km²)
                          Lower resolution = fewer API calls but less precise
            batch_size: Number of photos to process in each batch
            recalculate: If True, recalculate even if already geocoded

        Returns:
            dict with statistics
        """
        stats = {
            "total_photos": 0,
            "processed_photos": 0,
            "skipped_photos": 0,
            "api_calls": 0,
            "errors": 0,
            "error_details": [],
        }

        # Query photos that need geocoding
        query = Q(gps_latitude__isnull=False, gps_longitude__isnull=False)
        if not recalculate:
            query &= Q(geo_coded_at__isnull=True)

        photos_to_geocode = Photo.objects.filter(query)
        stats["total_photos"] = photos_to_geocode.count()

        if stats["total_photos"] == 0:
            self.progress_callback("No photos to geocode")
            return stats

        self.progress_callback(
            f"Geocoding {stats['total_photos']} photos using H3 resolution {h3_resolution}"
        )

        # Group photos by H3 cell
        h3_groups = self._group_photos_by_h3(photos_to_geocode, h3_resolution)
        self.progress_callback(f"Grouped into {len(h3_groups)} unique locations")

        # Geocode each H3 cell (one API call per cell)
        for h3_cell, photos in h3_groups.items():
            try:
                # Get representative coordinates (H3 cell center)
                lat, lon = h3.cell_to_latlng(h3_cell)

                # Call Google Geocoding API
                location_data = self._geocode_coordinates(lat, lon)
                stats["api_calls"] += 1

                if location_data:
                    # Apply to all photos in this H3 cell
                    self._apply_geocoding_to_photos(photos, location_data)
                    stats["processed_photos"] += len(photos)
                else:
                    stats["skipped_photos"] += len(photos)
                    logger.warning(f"No geocoding result for H3 cell {h3_cell}")

                # Progress update
                if stats["api_calls"] % 10 == 0:
                    self.progress_callback(
                        f"Processed {stats['processed_photos']}/{stats['total_photos']} photos "
                        f"({stats['api_calls']} API calls)"
                    )

            except Exception as e:
                error_msg = f"Error geocoding H3 cell {h3_cell}: {e}"
                logger.error(error_msg)
                stats["errors"] += 1
                stats["error_details"].append(error_msg)
                stats["skipped_photos"] += len(photos)

        return stats

    def _group_photos_by_h3(self, photos_queryset, resolution: int) -> dict:
        """
        Group photos by H3 cell at given resolution.

        Returns:
            dict mapping h3_cell -> list of Photo objects
        """
        h3_field = f"h3_res_{resolution}"
        groups = defaultdict(list)

        for photo in photos_queryset.iterator():
            h3_cell = getattr(photo, h3_field, None)
            if h3_cell:
                groups[h3_cell].append(photo)

        return dict(groups)

    def _geocode_coordinates(self, lat: float, lon: float) -> Optional[dict]:
        """
        Call Google Geocoding API to get location information.

        Args:
            lat: Latitude
            lon: Longitude

        Returns:
            dict with location info or None if failed
        """
        try:
            results = self.gmaps.reverse_geocode((lat, lon))  # type: ignore[attr-defined]

            if not results:
                return None

            # Parse first result
            result = results[0]
            location_data = {
                "formatted_address": result.get("formatted_address", ""),
                "country_code": None,
                "timezone_id": None,
            }

            # Extract country code
            for component in result.get("address_components", []):
                if "country" in component.get("types", []):
                    location_data["country_code"] = component.get("short_name", "")
                    break

            # Get timezone
            timezone_result = self.gmaps.timezone((lat, lon))  # type: ignore[attr-defined]
            if timezone_result and timezone_result.get("status") == "OK":
                location_data["timezone_id"] = timezone_result.get("timeZoneId")

            return location_data

        except Exception as e:
            logger.error(f"Geocoding API error for ({lat}, {lon}): {e}")
            return None

    def _apply_geocoding_to_photos(self, photos: list, location_data: dict):
        """
        Apply geocoding results to a batch of photos.

        Args:
            photos: List of Photo objects
            location_data: Dict with geocoding results
        """
        now = django_timezone.now()

        for photo in photos:
            photo.location = location_data.get("formatted_address", "")[:255]
            photo.country_code = location_data.get("country_code", "")[:2]
            photo.geo_coded_at = now

            # Calculate timezone-aware timestamp if we have timezone and original datetime
            if location_data.get("timezone_id") and photo.date_time_original_text:
                self._calculate_timezone_aware_datetime(photo, location_data["timezone_id"])

        # Bulk update
        Photo.objects.bulk_update(
            photos, ["location", "country_code", "geo_coded_at", "date_time_taken"]
        )

    def _calculate_timezone_aware_datetime(self, photo: Photo, timezone_id: str):
        """
        Calculate timezone-aware datetime from original text and timezone.

        Args:
            photo: Photo object
            timezone_id: IANA timezone identifier (e.g., 'America/Los_Angeles')
        """
        try:
            import pytz

            # Parse original datetime text (EXIF format: "2023:10:15 14:30:25")
            dt_text = photo.date_time_original_text.replace(":", "-", 2)  # Fix date part
            naive_dt = datetime.strptime(dt_text, "%Y-%m-%d %H:%M:%S")

            # Apply timezone
            tz = pytz.timezone(timezone_id)
            aware_dt = tz.localize(naive_dt)

            photo.date_time_taken = aware_dt

        except Exception as e:
            logger.warning(
                f"Could not calculate timezone-aware datetime for {photo.file_name}: {e}"
            )
