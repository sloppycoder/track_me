"""Unit tests for the smart-search parser (track_me.search).

Ported from the legacy ``tests/test_smart_search.py`` parser tests; the old API
integration tests were dropped with the ``myphoto`` app.
"""

from track_me.search import parse_smart_search


class TestSmartSearchParser:
    def test_empty_query(self):
        result = parse_smart_search("")
        assert result["search_type"] == "unknown"
        assert result["date_from"] is None
        assert result["date_to"] is None

    def test_country_code_uppercase(self):
        result = parse_smart_search("US")
        assert result["search_type"] == "country_code"
        assert result["country_code"] == "US"
        assert result["text_search"] is None

    def test_country_code_sg(self):
        result = parse_smart_search("SG")
        assert result["search_type"] == "country_code"
        assert result["country_code"] == "SG"

    def test_lowercase_not_country_code(self):
        result = parse_smart_search("us")
        assert result["search_type"] == "location"
        assert result["text_search"] == "us"

    def test_year_search(self):
        result = parse_smart_search("2004")
        assert result["search_type"] == "date"
        assert result["date_from"].year == 2004
        assert result["date_from"].month == 1
        assert result["date_from"].day == 1
        assert result["date_to"].year == 2004
        assert result["date_to"].month == 12
        assert result["date_to"].day == 31

    def test_month_year_search(self):
        result = parse_smart_search("jan 2004")
        assert result["search_type"] == "date"
        assert result["date_from"].year == 2004
        assert result["date_from"].month == 1
        assert result["date_to"].year == 2004
        assert result["date_to"].month == 1

    def test_iso_date_search(self):
        result = parse_smart_search("2024-12-23")
        assert result["search_type"] == "date"
        assert result["date_from"] is not None
        assert result["date_to"] is not None

    def test_date_range_with_to(self):
        result = parse_smart_search("2004 to 2006")
        assert result["search_type"] == "date_range"
        assert result["date_from"].year == 2004
        assert result["date_to"].year == 2006

    def test_date_range_month_year(self):
        result = parse_smart_search("jan 2004 to dec 2005")
        assert result["search_type"] == "date_range"
        assert result["date_from"].year == 2004
        assert result["date_from"].month == 1
        assert result["date_to"].year == 2005
        assert result["date_to"].month == 12

    def test_date_range_with_dash(self):
        result = parse_smart_search("2004 - 2006")
        assert result["search_type"] == "date_range"
        assert result["date_from"].year == 2004
        assert result["date_to"].year == 2006

    def test_location_search(self):
        result = parse_smart_search("Singapore")
        assert result["search_type"] == "location"
        assert result["text_search"] == "Singapore"
        assert result["date_from"] is None

    def test_location_with_spaces(self):
        result = parse_smart_search("New York")
        assert result["search_type"] == "location"
        assert result["text_search"] == "New York"

    def test_location_with_comma(self):
        result = parse_smart_search("Tokyo, Japan")
        assert result["search_type"] == "location"
        assert result["text_search"] == "Tokyo, Japan"

    def test_three_letter_code_not_country(self):
        result = parse_smart_search("USA")
        assert result["search_type"] == "location"
        assert result["text_search"] == "USA"
