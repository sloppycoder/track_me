"""
Unit tests for smart search parser.
"""

import pytest
from django.utils import timezone

from myphoto.views import parse_smart_search


class TestSmartSearchParser:
    """Tests for the parse_smart_search function."""

    def test_empty_query(self):
        """Test empty query returns unknown type."""
        result = parse_smart_search("")
        assert result["search_type"] == "unknown"
        assert result["date_from"] is None
        assert result["date_to"] is None

    def test_country_code_uppercase(self):
        """Test 2-letter uppercase country code."""
        result = parse_smart_search("US")
        assert result["search_type"] == "country_code"
        assert result["country_code"] == "US"
        assert result["text_search"] is None

    def test_country_code_sg(self):
        """Test Singapore country code."""
        result = parse_smart_search("SG")
        assert result["search_type"] == "country_code"
        assert result["country_code"] == "SG"

    def test_lowercase_not_country_code(self):
        """Test lowercase 2 letters is not treated as country code."""
        result = parse_smart_search("us")
        assert result["search_type"] == "location"
        assert result["text_search"] == "us"

    def test_year_search(self):
        """Test searching by year."""
        result = parse_smart_search("2004")
        assert result["search_type"] == "date"
        assert result["date_from"] is not None
        assert result["date_to"] is not None
        assert result["date_from"].year == 2004
        assert result["date_from"].month == 1
        assert result["date_from"].day == 1
        assert result["date_to"].year == 2004
        assert result["date_to"].month == 12
        assert result["date_to"].day == 31

    def test_month_year_search(self):
        """Test searching by month and year."""
        result = parse_smart_search("jan 2004")
        assert result["search_type"] == "date"
        assert result["date_from"] is not None
        assert result["date_to"] is not None
        assert result["date_from"].year == 2004
        assert result["date_from"].month == 1
        assert result["date_to"].year == 2004
        assert result["date_to"].month == 1

    def test_iso_date_search(self):
        """Test searching by ISO date."""
        result = parse_smart_search("2024-12-23")
        assert result["search_type"] == "date"
        assert result["date_from"] is not None
        assert result["date_to"] is not None

    def test_date_range_with_to(self):
        """Test date range with 'to' separator."""
        result = parse_smart_search("2004 to 2006")
        assert result["search_type"] == "date_range"
        assert result["date_from"] is not None
        assert result["date_to"] is not None
        assert result["date_from"].year == 2004
        assert result["date_to"].year == 2006

    def test_date_range_month_year(self):
        """Test date range with month-year format."""
        result = parse_smart_search("jan 2004 to dec 2005")
        assert result["search_type"] == "date_range"
        assert result["date_from"] is not None
        assert result["date_to"] is not None
        assert result["date_from"].year == 2004
        assert result["date_from"].month == 1
        assert result["date_to"].year == 2005
        assert result["date_to"].month == 12

    def test_date_range_with_dash(self):
        """Test date range with dash separator."""
        result = parse_smart_search("2004 - 2006")
        assert result["search_type"] == "date_range"
        assert result["date_from"].year == 2004
        assert result["date_to"].year == 2006

    def test_location_search(self):
        """Test location text search."""
        result = parse_smart_search("Singapore")
        assert result["search_type"] == "location"
        assert result["text_search"] == "Singapore"
        assert result["date_from"] is None

    def test_location_with_spaces(self):
        """Test location with multiple words."""
        result = parse_smart_search("New York")
        assert result["search_type"] == "location"
        assert result["text_search"] == "New York"

    def test_location_with_comma(self):
        """Test location with comma."""
        result = parse_smart_search("Tokyo, Japan")
        assert result["search_type"] == "location"
        assert result["text_search"] == "Tokyo, Japan"

    def test_three_letter_code_not_country(self):
        """Test 3-letter code is treated as location, not country."""
        result = parse_smart_search("USA")
        assert result["search_type"] == "location"
        assert result["text_search"] == "USA"


class TestSmartSearchIntegration:
    """Integration tests for smart search with actual API."""

    def test_search_by_country_code(self, client, sample_photo):
        """Test searching by country code."""
        from django.urls import reverse

        # Update photo to have SG country code
        sample_photo.country_code = "SG"
        sample_photo.save()

        response = client.get(reverse("myphoto:api_search"), {"q": "SG"})
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1

    def test_search_by_location(self, client, sample_photo):
        """Test searching by location text."""
        from django.urls import reverse

        response = client.get(reverse("myphoto:api_search"), {"q": "Singapore"})
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1

    def test_search_by_year(self, client, db):
        """Test searching by year."""
        from datetime import datetime

        from django.urls import reverse

        from myphoto.models import Photo

        # Create photos in 2004
        for i in range(3):
            Photo.objects.create(
                file_name=f"test_{i}.jpg",
                source_file=f"2004/test_{i}.jpg",
                date_time_taken=timezone.make_aware(datetime(2004, 6, 15, 12, 0, 0)),
            )

        # Create photo in 2005
        Photo.objects.create(
            file_name="test_2005.jpg",
            source_file="2005/test.jpg",
            date_time_taken=timezone.make_aware(datetime(2005, 6, 15, 12, 0, 0)),
        )

        response = client.get(reverse("myphoto:api_search"), {"q": "2004"})
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 3

    def test_search_by_date_range(self, client, db):
        """Test searching by date range."""
        from datetime import datetime

        from django.urls import reverse

        from myphoto.models import Photo

        # Create photos in different years
        Photo.objects.create(
            file_name="2003.jpg",
            source_file="2003.jpg",
            date_time_taken=timezone.make_aware(datetime(2003, 1, 1)),
        )
        Photo.objects.create(
            file_name="2004.jpg",
            source_file="2004.jpg",
            date_time_taken=timezone.make_aware(datetime(2004, 6, 1)),
        )
        Photo.objects.create(
            file_name="2005.jpg",
            source_file="2005.jpg",
            date_time_taken=timezone.make_aware(datetime(2005, 6, 1)),
        )
        Photo.objects.create(
            file_name="2007.jpg",
            source_file="2007.jpg",
            date_time_taken=timezone.make_aware(datetime(2007, 1, 1)),
        )

        response = client.get(reverse("myphoto:api_search"), {"q": "2004 to 2005"})
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2


@pytest.fixture
def sample_photo(db):
    """Create a sample photo for testing."""
    from decimal import Decimal

    from myphoto.models import Photo

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
