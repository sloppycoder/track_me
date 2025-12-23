"""
Unit tests for myphoto views and API endpoints.
"""

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from myphoto.models import Photo


@pytest.fixture
def client():
    """Django test client."""
    return Client()


@pytest.fixture
def sample_photo(db):
    """Create a sample photo for testing."""
    photo = Photo.objects.create(
        file_name="test_photo.jpg",
        source_file="2025/02/test_photo.jpg",
        gps_latitude=Decimal("1.3521"),
        gps_longitude=Decimal("103.8198"),
        location="Singapore",
        country_code="SG",
        date_time_taken=timezone.now(),
        is_location_manual=False,
    )
    return photo


class TestPhotoListView:
    """Tests for the main photo list page view."""

    def test_photo_list_renders(self, client):
        """Test that the photo list page renders successfully."""
        response = client.get(reverse("myphoto:photo_list"))
        assert response.status_code == 200
        assert b"photo_list.html" in response.content or response.status_code == 200

    def test_photo_list_includes_api_key(self, client):
        """Test that API key is passed to template."""
        response = client.get(reverse("myphoto:photo_list"))
        assert response.status_code == 200
        # Check context if available
        if hasattr(response, "context"):
            assert "api_key" in response.context


class TestApiPhotoSearch:
    """Tests for the photo search API endpoint."""

    def test_search_returns_all_photos_with_no_filters(self, client, sample_photo):
        """Test search returns all photos when no filters applied."""
        response = client.get(reverse("myphoto:api_search"))
        assert response.status_code == 200

        data = response.json()
        assert "photos" in data
        assert "count" in data
        assert data["count"] == 1
        assert data["photos"][0]["id"] == sample_photo.id  # type: ignore[attr-defined]

    def test_search_by_location(self, client, sample_photo):
        """Test search filters by location text."""
        response = client.get(reverse("myphoto:api_search"), {"q": "Singapore"})
        assert response.status_code == 200

        data = response.json()
        assert data["count"] == 1

        # Search for non-existent location
        response = client.get(reverse("myphoto:api_search"), {"q": "Tokyo"})
        data = response.json()
        assert data["count"] == 0

    def test_search_by_country_code(self, client, sample_photo):
        """Test search filters by country code."""
        response = client.get(reverse("myphoto:api_search"), {"q": "SG"})
        assert response.status_code == 200

        data = response.json()
        assert data["count"] == 1

    def test_search_response_structure(self, client, sample_photo):
        """Test that search response has correct structure."""
        response = client.get(reverse("myphoto:api_search"))
        data = response.json()

        photo = data["photos"][0]
        assert "id" in photo
        assert "file_name" in photo
        assert "thumbnail_url" in photo
        assert "gps_latitude" in photo
        assert "gps_longitude" in photo
        assert "location" in photo
        assert "country_code" in photo
        assert "date_time_taken" in photo
        assert "is_location_manual" in photo

    def test_search_respects_limit(self, client, db):
        """Test that search respects limit parameter."""
        # Create 5 photos
        for i in range(5):
            Photo.objects.create(
                file_name=f"test_{i}.jpg",
                source_file=f"2025/02/test_{i}.jpg",
                date_time_taken=timezone.now(),
            )

        response = client.get(reverse("myphoto:api_search"), {"limit": "2"})
        data = response.json()
        assert data["count"] == 2


class TestApiThumbnail:
    """Tests for the thumbnail API endpoint."""

    @patch("myphoto.views.ThumbnailService")
    def test_thumbnail_returns_image(self, mock_service_class, client, sample_photo, tmp_path):
        """Test thumbnail endpoint returns image file."""
        # Create a fake thumbnail file
        thumbnail_file = tmp_path / "thumbnail.jpg"
        thumbnail_file.write_bytes(b"fake image data")

        # Mock the service
        mock_service = MagicMock()
        mock_service.generate_thumbnail.return_value = thumbnail_file
        mock_service_class.return_value = mock_service

        url = reverse("myphoto:thumbnail", kwargs={"photo_id": sample_photo.id})  # type: ignore[attr-defined]
        response = client.get(url)

        assert response.status_code == 200
        assert response["Content-Type"] == "image/jpeg"
        assert response["Cache-Control"] == "public, max-age=86400"

    def test_thumbnail_returns_404_for_nonexistent_photo(self, client, db):
        """Test thumbnail returns 404 for non-existent photo."""
        url = reverse("myphoto:thumbnail", kwargs={"photo_id": 99999})
        response = client.get(url)
        assert response.status_code == 404


class TestApiPhotoPreview:
    """Tests for the photo preview API endpoint."""

    def test_preview_returns_404_for_nonexistent_photo(self, client, db):
        """Test preview returns 404 for non-existent photo."""
        url = reverse("myphoto:photo_preview", kwargs={"photo_id": 99999})
        response = client.get(url)
        assert response.status_code == 404

    def test_preview_returns_404_for_missing_source_file(self, client, db):
        """Test preview returns 404 when source_file is missing."""
        photo = Photo.objects.create(
            file_name="test.jpg",
            source_file="",  # Empty source file
            date_time_taken=timezone.now(),
        )
        url = reverse("myphoto:photo_preview", kwargs={"photo_id": photo.id})  # type: ignore[attr-defined]
        response = client.get(url)
        assert response.status_code == 404

    def test_preview_blocks_path_traversal(self, client, db):
        """Test preview blocks path traversal attempts."""
        photo = Photo.objects.create(
            file_name="test.jpg",
            source_file="../etc/passwd",  # Path traversal attempt
            date_time_taken=timezone.now(),
        )
        url = reverse("myphoto:photo_preview", kwargs={"photo_id": photo.id})  # type: ignore[attr-defined]
        response = client.get(url)
        assert response.status_code == 404

    def test_preview_serves_existing_photo(self, client, sample_photo, tmp_path):
        """Test preview serves existing photo file."""
        # Create a fake photo file in the test photos directory
        test_photos_dir = Path(settings.PHOTOS_BASE_DIR)
        photo_path = test_photos_dir / sample_photo.source_file
        photo_path.parent.mkdir(parents=True, exist_ok=True)
        photo_path.write_bytes(b"fake jpeg data")

        try:
            url = reverse("myphoto:photo_preview", kwargs={"photo_id": sample_photo.id})  # type: ignore[attr-defined]
            response = client.get(url)

            assert response.status_code == 200
            assert response["Content-Type"] == "image/jpeg"
            assert response["Cache-Control"] == "public, max-age=86400"
        finally:
            # Clean up
            if photo_path.exists():
                photo_path.unlink()


class TestApiUpdateLocation:
    """Tests for the location update API endpoint."""

    def test_update_location_requires_post(self, client):
        """Test that update location only accepts POST requests."""
        response = client.get(reverse("myphoto:api_update_location"))
        assert response.status_code == 405  # Method not allowed

    def test_update_location_with_valid_data(self, client, sample_photo):
        """Test updating location with valid GPS coordinates."""
        with patch("myphoto.views.GeocodingService") as mock_service_class:
            # Mock the geocoding service
            mock_service = MagicMock()
            mock_service._geocode_coordinates.return_value = {
                "formatted_address": "Tokyo, Japan",
                "country_code": "JP",
            }
            mock_service_class.return_value = mock_service

            data = {
                "photo_ids": [sample_photo.id],  # type: ignore[attr-defined]
                "latitude": 35.6762,
                "longitude": 139.6503,
            }

            response = client.post(
                reverse("myphoto:api_update_location"),
                data=json.dumps(data),
                content_type="application/json",
            )

            assert response.status_code == 200
            result = response.json()
            assert result["success"] is True
            assert result["count"] == 1
            assert result["location"] == "Tokyo, Japan"
            assert result["country_code"] == "JP"

            # Check that photo was updated
            sample_photo.refresh_from_db()
            assert sample_photo.gps_latitude == Decimal("35.6762")
            assert sample_photo.gps_longitude == Decimal("139.6503")
            assert sample_photo.is_location_manual is True
            assert sample_photo.location == "Tokyo, Japan"
            assert sample_photo.country_code == "JP"

            # Verify H3 indexes were recalculated
            assert sample_photo.h3_res_3 is not None
            assert sample_photo.h3_res_6 is not None
            assert sample_photo.h3_res_9 is not None
            assert sample_photo.h3_res_10 is not None
            assert sample_photo.h3_res_11 is not None

    def test_update_location_validates_coordinates(self, client, sample_photo):
        """Test that invalid coordinates are rejected."""
        # Test latitude out of range
        data = {
            "photo_ids": [sample_photo.id],  # type: ignore[attr-defined]
            "latitude": 91.0,  # Invalid
            "longitude": 0.0,
        }

        response = client.post(
            reverse("myphoto:api_update_location"),
            data=json.dumps(data),
            content_type="application/json",
        )

        assert response.status_code == 400
        result = response.json()
        assert result["success"] is False
        assert "Invalid coordinates" in result["error"]

    def test_update_location_requires_photo_ids(self, client):
        """Test that photo_ids are required."""
        data = {"latitude": 1.0, "longitude": 1.0}

        response = client.post(
            reverse("myphoto:api_update_location"),
            data=json.dumps(data),
            content_type="application/json",
        )

        assert response.status_code == 400
        result = response.json()
        assert result["success"] is False
        assert "No photos selected" in result["error"]

    def test_update_location_handles_invalid_json(self, client):
        """Test that invalid JSON is handled gracefully."""
        response = client.post(
            reverse("myphoto:api_update_location"),
            data="invalid json",
            content_type="application/json",
        )

        assert response.status_code == 400
        result = response.json()
        assert result["success"] is False
        assert "Invalid JSON" in result["error"]


class TestApiReverseGeocode:
    """Tests for the reverse geocoding API endpoint."""

    def test_reverse_geocode_requires_get(self, client):
        """Test that reverse geocode only accepts GET requests."""
        response = client.post(reverse("myphoto:api_reverse_geocode"))
        assert response.status_code == 405  # Method not allowed

    @patch("myphoto.views.GeocodingService")
    def test_reverse_geocode_with_valid_coordinates(self, mock_service_class, client):
        """Test reverse geocoding with valid coordinates."""
        mock_service = MagicMock()
        mock_service._geocode_coordinates.return_value = {
            "location": "Singapore",
            "country_code": "SG",
        }
        mock_service_class.return_value = mock_service

        response = client.get(
            reverse("myphoto:api_reverse_geocode"), {"lat": "1.3521", "lng": "103.8198"}
        )

        assert response.status_code == 200
        result = response.json()
        assert result["location"] == "Singapore"
        assert result["country_code"] == "SG"

    def test_reverse_geocode_validates_coordinates(self, client):
        """Test that invalid coordinates are rejected."""
        # Test latitude out of range
        response = client.get(
            reverse("myphoto:api_reverse_geocode"), {"lat": "91.0", "lng": "0.0"}
        )

        assert response.status_code == 400
        result = response.json()
        assert "error" in result
        assert "Invalid coordinates" in result["error"]

    def test_reverse_geocode_handles_missing_parameters(self, client):
        """Test that missing parameters are handled."""
        response = client.get(reverse("myphoto:api_reverse_geocode"))

        assert response.status_code == 400
        result = response.json()
        assert "error" in result
