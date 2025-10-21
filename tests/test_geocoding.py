"""
Tests for geocoding service with mocked Google Maps API responses.
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from myphoto.models import Photo
from myphoto.services.geocoding_service import GeocodingService


@pytest.fixture
def mock_google_maps_client():
    """Mock Google Maps client."""
    # Mock the googlemaps module at import time
    mock_gm = MagicMock()
    client = MagicMock()
    mock_gm.Client.return_value = client

    with patch.dict("sys.modules", {"googlemaps": mock_gm}):
        yield client


@pytest.fixture
def sample_geocoding_response():
    """Sample Google Geocoding API response."""
    return [
        {
            "formatted_address": "1600 Amphitheatre Parkway, Mountain View, CA, USA",
            "address_components": [
                {"short_name": "US", "types": ["country"]},
                {"short_name": "CA", "types": ["administrative_area_level_1"]},
            ],
        }
    ]


@pytest.fixture
def sample_timezone_response():
    """Sample Google Timezone API response."""
    return {"status": "OK", "timeZoneId": "America/Los_Angeles"}


@pytest.mark.django_db
class TestGeocodingService:
    """Test the GeocodingService."""

    def test_geocoding_service_requires_api_key(self):
        """Test that service requires API key."""
        with pytest.raises(ValueError, match="Google Maps API key required"):
            GeocodingService()

    def test_geocode_single_h3_cell(
        self, mock_google_maps_client, sample_geocoding_response, sample_timezone_response
    ):
        """Test geocoding photos grouped in single H3 cell."""
        # Setup mock responses
        mock_google_maps_client.reverse_geocode.return_value = sample_geocoding_response
        mock_google_maps_client.timezone.return_value = sample_timezone_response

        # Create test photos at same location (will share H3 cell)
        photo1 = Photo.objects.create(
            source_file="photo1.jpg",
            file_name="photo1.jpg",
            directory=".",
            gps_latitude=Decimal("37.7749"),
            gps_longitude=Decimal("-122.4194"),
        )
        photo1.calculate_h3_indexes()
        photo1.save()

        photo2 = Photo.objects.create(
            source_file="photo2.jpg",
            file_name="photo2.jpg",
            directory=".",
            gps_latitude=Decimal("37.7750"),  # Very close
            gps_longitude=Decimal("-122.4195"),
        )
        photo2.calculate_h3_indexes()
        photo2.save()

        # Geocode
        service = GeocodingService(google_api_key="test_key")
        stats = service.geocode_photos(h3_resolution=9, recalculate=False)

        # Verify results
        assert stats["total_photos"] == 2
        assert stats["processed_photos"] == 2
        assert stats["api_calls"] == 1  # Only one API call for both photos!

        # Verify photos were updated
        photo1.refresh_from_db()
        photo2.refresh_from_db()

        assert photo1.location == "1600 Amphitheatre Parkway, Mountain View, CA, USA"
        assert photo1.country_code == "US"
        assert photo1.geo_coded_at is not None

        assert photo2.location == "1600 Amphitheatre Parkway, Mountain View, CA, USA"
        assert photo2.country_code == "US"
        assert photo2.geo_coded_at is not None

    def test_geocode_multiple_h3_cells(
        self, mock_google_maps_client, sample_geocoding_response, sample_timezone_response
    ):
        """Test geocoding photos in different H3 cells."""
        # Setup mock responses
        mock_google_maps_client.reverse_geocode.return_value = sample_geocoding_response
        mock_google_maps_client.timezone.return_value = sample_timezone_response

        # Create photos at different locations
        photo1 = Photo.objects.create(
            source_file="photo1.jpg",
            file_name="photo1.jpg",
            directory=".",
            gps_latitude=Decimal("37.7749"),  # San Francisco
            gps_longitude=Decimal("-122.4194"),
        )
        photo1.calculate_h3_indexes()
        photo1.save()

        photo2 = Photo.objects.create(
            source_file="photo2.jpg",
            file_name="photo2.jpg",
            directory=".",
            gps_latitude=Decimal("34.0522"),  # Los Angeles (different H3 cell)
            gps_longitude=Decimal("-118.2437"),
        )
        photo2.calculate_h3_indexes()
        photo2.save()

        # Geocode
        service = GeocodingService(google_api_key="test_key")
        stats = service.geocode_photos(h3_resolution=9, recalculate=False)

        # Verify results
        assert stats["total_photos"] == 2
        assert stats["processed_photos"] == 2
        assert stats["api_calls"] == 2  # Two API calls for different locations

    def test_geocode_skip_already_geocoded(
        self, mock_google_maps_client, sample_geocoding_response, sample_timezone_response
    ):
        """Test that already geocoded photos are skipped."""
        # Create already geocoded photo
        photo = Photo.objects.create(
            source_file="photo1.jpg",
            file_name="photo1.jpg",
            directory=".",
            gps_latitude=Decimal("37.7749"),
            gps_longitude=Decimal("-122.4194"),
            geo_coded_at=timezone.now(),  # Already geocoded
            location="Existing location",
            country_code="US",
        )
        photo.calculate_h3_indexes()
        photo.save()

        # Geocode
        service = GeocodingService(google_api_key="test_key")
        stats = service.geocode_photos(h3_resolution=9, recalculate=False)

        # Should skip
        assert stats["total_photos"] == 0
        assert stats["api_calls"] == 0

    def test_geocode_with_recalculate(
        self, mock_google_maps_client, sample_geocoding_response, sample_timezone_response
    ):
        """Test recalculate option forces re-geocoding."""
        # Setup mock responses
        mock_google_maps_client.reverse_geocode.return_value = sample_geocoding_response
        mock_google_maps_client.timezone.return_value = sample_timezone_response

        # Create already geocoded photo
        photo = Photo.objects.create(
            source_file="photo1.jpg",
            file_name="photo1.jpg",
            directory=".",
            gps_latitude=Decimal("37.7749"),
            gps_longitude=Decimal("-122.4194"),
            geo_coded_at=timezone.now(),
            location="Old location",
            country_code="XX",
        )
        photo.calculate_h3_indexes()
        photo.save()

        # Geocode with recalculate=True
        service = GeocodingService(google_api_key="test_key")
        stats = service.geocode_photos(h3_resolution=9, recalculate=True)

        # Should re-geocode
        assert stats["total_photos"] == 1
        assert stats["processed_photos"] == 1
        assert stats["api_calls"] == 1

        # Verify updated
        photo.refresh_from_db()
        assert photo.location == "1600 Amphitheatre Parkway, Mountain View, CA, USA"
        assert photo.country_code == "US"

    def test_timezone_info_retrieved(
        self, mock_google_maps_client, sample_geocoding_response, sample_timezone_response
    ):
        """Test that timezone info is retrieved from Google API."""
        # Setup mock responses
        mock_google_maps_client.reverse_geocode.return_value = sample_geocoding_response
        mock_google_maps_client.timezone.return_value = sample_timezone_response

        # Create photo with original datetime text
        photo = Photo.objects.create(
            source_file="photo1.jpg",
            file_name="photo1.jpg",
            directory=".",
            gps_latitude=Decimal("37.7749"),
            gps_longitude=Decimal("-122.4194"),
            date_time_original_text="2023:10:15 14:30:25",  # EXIF format
        )
        photo.calculate_h3_indexes()
        photo.save()

        # Geocode
        service = GeocodingService(google_api_key="test_key")
        stats = service.geocode_photos(h3_resolution=9)

        # Verify geocoding was successful
        assert stats["processed_photos"] == 1

        # Verify timezone API was called
        assert mock_google_maps_client.timezone.called

        # Verify location was set
        photo.refresh_from_db()
        assert photo.location is not None
        assert photo.country_code == "US"

    def test_geocode_photos_without_gps(self, mock_google_maps_client):
        """Test that photos without GPS are not geocoded."""
        # Create photo without GPS
        photo = Photo.objects.create(
            source_file="photo1.jpg",
            file_name="photo1.jpg",
            directory=".",
            # No GPS coordinates
        )
        photo.save()

        # Geocode
        service = GeocodingService(google_api_key="test_key")
        stats = service.geocode_photos(h3_resolution=9)

        # Should skip
        assert stats["total_photos"] == 0
        assert stats["api_calls"] == 0
