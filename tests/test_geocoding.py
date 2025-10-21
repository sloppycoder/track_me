"""
Tests for geocoding service using live Google Maps API.
"""

import os
from pathlib import Path

import pytest

from myphoto.services.geocoding_service import GeocodingService
from myphoto.services.photo_processing_service import PhotoProcessingService

# Test photos directory
TEST_PHOTOS_DIR = Path(__file__).parent / "test_photos"


@pytest.mark.django_db
class TestGeocodingService:
    """Test the GeocodingService with live Google Maps API."""

    def test_geocoding_service_requires_api_key(self, monkeypatch):
        """Test that service requires API key."""
        # Temporarily remove API key from settings
        from django.conf import settings

        monkeypatch.delattr(settings, "GOOGLE_MAPS_API_KEY", raising=False)

        with pytest.raises(ValueError, match="Google Maps API key required"):
            GeocodingService()

    @pytest.mark.skipif(
        not os.environ.get("GOOGLE_MAPS_API_KEY", ""),
        reason="No GoogleMaps API key",
    )
    def test_geocode_heic_photo_live_api(self):
        """Test geocoding HEIC photo with live Google Maps API (Vietnam location)."""
        # Process HEIC file first to extract GPS
        photo_rel_path = "2020/01/IMG_4584.HEIC"
        photo_path = TEST_PHOTOS_DIR / photo_rel_path

        photo_service = PhotoProcessingService()
        result = photo_service.process_single_photo(
            str(photo_path),
            str(TEST_PHOTOS_DIR),
            force_reprocess=False,
        )

        assert result["action"] == "created"
        photo = result["photo"]

        # Verify GPS was extracted
        assert photo.gps_latitude is not None
        assert photo.gps_longitude is not None
        assert photo.has_gps is True
        assert photo.has_h3_indexes is True

        print("\n=== GPS Coordinates ===")
        print(f"Latitude: {photo.gps_latitude}")
        print(f"Longitude: {photo.gps_longitude}")
        print(f"H3 Index (res 9): {photo.h3_res_9}")

        # Geocode using live Google Maps API
        # Note: Requires GOOGLE_MAPS_API_KEY in settings
        geocoding_service = GeocodingService()

        # Call API directly to print response
        import googlemaps
        from django.conf import settings

        gmaps = googlemaps.Client(key=settings.GOOGLE_MAPS_API_KEY)
        api_response = gmaps.reverse_geocode(  # type: ignore
            (float(photo.gps_latitude), float(photo.gps_longitude))
        )

        print("\n=== Google Maps API Response ===")
        if api_response:
            print(f"Formatted Address: {api_response[0].get('formatted_address')}")
            print("\nAddress Components:")
            for component in api_response[0].get("address_components", []):
                long_name = component.get("long_name")
                short_name = component.get("short_name")
                types = component.get("types")
                print(f"  {long_name} ({short_name}): {types}")

        timezone_response = gmaps.timezone(  # type: ignore
            (float(photo.gps_latitude), float(photo.gps_longitude))
        )
        print("\n=== Timezone API Response ===")
        print(f"Timezone ID: {timezone_response.get('timeZoneId')}")
        print(f"Timezone Name: {timezone_response.get('timeZoneName')}")

        # Now run the geocoding service with h3_resolution=12
        # Resolution 12 = ~0.3km² hexagon
        stats = geocoding_service.geocode_photos(h3_resolution=12, recalculate=False)

        # Verify geocoding happened
        assert stats["total_photos"] == 1
        assert stats["processed_photos"] == 1
        assert stats["api_calls"] == 1
        assert stats["errors"] == 0

        # Verify location data
        photo.refresh_from_db()

        print("\n=== Photo After Geocoding ===")
        print(f"Location: {photo.location}")
        print(f"Country Code: {photo.country_code}")
        print(f"Date Time Taken: {photo.date_time_taken}")

        assert photo.geo_coded_at is not None
        assert photo.location is not None
        assert photo.country_code == "VN"  # Vietnam
        assert "Vietnam" in photo.location or "Viet Nam" in photo.location

        # Verify timezone was set
        assert photo.date_time_taken is not None
