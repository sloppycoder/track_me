"""Offline timezone lookup from coordinates.

Uses timezonefinder (bundled boundary data, no network/API) to map a lat/lon to
an IANA zone like "Asia/Tokyo". Combined with the stdlib zoneinfo (DST + historical
rules), this gives correct *local* time for a photo's location — which is what the
timeline must bucket on so international travel aligns to the right local day.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_finder = None


def _tf():
    global _finder
    if _finder is None:
        from timezonefinder import TimezoneFinder

        _finder = TimezoneFinder()
    return _finder


def timezone_for(lat, lon) -> str | None:
    """Return the IANA timezone name for a coordinate, or None."""
    try:
        return _tf().timezone_at(lng=float(lon), lat=float(lat))
    except Exception as e:  # bad coords / lookup failure
        logger.warning("Timezone lookup failed for (%s, %s): %s", lat, lon, e)
        return None
