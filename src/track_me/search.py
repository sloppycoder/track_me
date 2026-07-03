"""Smart-search query parsing.

Salvaged from the legacy ``myphoto.views`` (the one piece of that app worth
keeping): turn a free-text search box into a structured intent — a date, a date
range, a 2-letter country code, or location text — without the user choosing a
mode. Pure functions, no model/ORM coupling, so the future search API/UI can
build a ``MediaItem`` query on top of the returned dict.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import UTC, datetime

import dateparser

# Month-name fragments that hint a token is a date rather than location text.
_MONTHS = (
    "jan",
    "feb",
    "mar",
    "apr",
    "may",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
)

# Separators that split a "X to Y" date range (various dash glyphs included).
_RANGE_SEPARATORS = (" to ", " - ", " – ", " — ")


def parse_date_filter(date_str: str) -> tuple[datetime | None, datetime | None]:
    """Parse a flexible human date into a ``(start, end)`` datetime window.

    Supports:
    - ``"2004"`` -> Jan 1 2004 .. Dec 31 2004
    - ``"jan 2004"`` / ``"January 2004"`` -> first .. last day of that month
    - ``"2005-01-01"`` -> start .. end of that day
    - many other ``dateparser``-understood formats

    Returns ``(None, None)`` when nothing parseable is found.
    """
    if not date_str:
        return None, None

    date_str = date_str.strip()

    parsed_date = dateparser.parse(
        date_str,
        settings={
            "PREFER_DATES_FROM": "past",
            "RELATIVE_BASE": datetime.now(),
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )
    if not parsed_date:
        return None, None

    # Year-only, e.g. "2004" -> whole year.
    if date_str.isdigit() and len(date_str) == 4:
        year = int(date_str)
        start = datetime(year, 1, 1, tzinfo=UTC)
        end = datetime(year, 12, 31, 23, 59, 59, tzinfo=UTC)
        return start, end

    # Month + year, e.g. "jan 2004" -> whole month.
    parts = date_str.lower().split()
    if len(parts) == 2 and parts[1].isdigit():
        year, month = parsed_date.year, parsed_date.month
        start = datetime(year, month, 1, tzinfo=UTC)
        last_day = monthrange(year, month)[1]
        end = datetime(year, month, last_day, 23, 59, 59, tzinfo=UTC)
        return start, end

    # Specific date -> start-of-day .. end-of-day.
    start = parsed_date.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=UTC)
    end = parsed_date.replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=UTC)
    return start, end


def parse_smart_search(query_str: str) -> dict:
    """Detect search intent from a free-text query.

    Detection order:
    1. Date range: ``"2004 to 2006"``, ``"jan 2004 to dec 2005"``
    2. Country code: 2 uppercase letters (``"US"``, ``"SG"``)
    3. Single date/year/month: ``"2004"``, ``"jan 2004"``, ``"2005-01-01"``
    4. Otherwise: location text

    Returns a dict with keys ``search_type`` (``date_range`` | ``date`` |
    ``country_code`` | ``location`` | ``unknown``), ``date_from``, ``date_to``,
    ``text_search``, ``country_code``.
    """
    base = {
        "search_type": "unknown",
        "date_from": None,
        "date_to": None,
        "text_search": None,
        "country_code": None,
    }

    if not query_str or not query_str.strip():
        return base

    query = query_str.strip()

    # 1. Date range: "X to Y" / "X - Y".
    for sep in _RANGE_SEPARATORS:
        if sep in query.lower():
            parts = query.lower().split(sep)
            if len(parts) == 2:
                start_date, _ = parse_date_filter(parts[0].strip())
                _, end_date = parse_date_filter(parts[1].strip())
                if start_date and end_date:
                    return {
                        **base,
                        "search_type": "date_range",
                        "date_from": start_date,
                        "date_to": end_date,
                    }
            break  # only consider the first matching separator

    # 2. Country code: exactly 2 uppercase letters.
    if len(query) == 2 and query.isupper() and query.isalpha():
        return {**base, "search_type": "country_code", "country_code": query}

    # 3. Single date/year/month — only attempt if it looks date-ish.
    date_indicators = (
        query.isdigit() and len(query) == 4,
        any(m in query.lower() for m in _MONTHS),
        "-" in query and any(c.isdigit() for c in query),
        "/" in query and any(c.isdigit() for c in query),
    )
    if any(date_indicators):
        start_date, end_date = parse_date_filter(query)
        if start_date and end_date:
            return {
                **base,
                "search_type": "date",
                "date_from": start_date,
                "date_to": end_date,
            }

    # 4. Fallback: location text.
    return {**base, "search_type": "location", "text_search": query}
