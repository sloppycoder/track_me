"""Reverse-geocode located MediaItems into place names + country codes.

Ported from the legacy ``geocoding_service`` (its H3-batching is the one genuinely
good idea in the old code) and reshaped to work on ``MediaItem``: photos are
grouped by a coarse H3 cell so each cell costs a single Google API call, and the
result is applied to every item in the cell.

Note: ``taken_at`` is NOT touched here. Sidecar-sourced timestamps are already
authoritative UTC instants; refining EXIF/file-mtime times via the location's
timezone is a separate, later concern.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import h3
from django.conf import settings
from django.db.models import Q
from django.utils import timezone as dj_tz

from library.models import MediaItem

logger = logging.getLogger(__name__)

# H3 resolution used to batch geocoding calls. Coarser = fewer API calls but a
# single label covers a wider area. res 9 ~ 0.1 km^2 (neighbourhood).
DEFAULT_GEOCODE_RESOLUTION = 9


def _pending_query(recalculate: bool) -> Q:
    """Located items still needing geocoding (or all located, if recalculate)."""
    query = Q(latitude__isnull=False, longitude__isnull=False)
    if not recalculate:
        query &= Q(geocoded_at__isnull=True)
    return query


def estimate_calls(
    resolutions: list[int], recalculate: bool = False
) -> tuple[int, dict[int, int]]:
    """Count distinct H3 cells (= API calls) per resolution, without any API call.

    Returns (pending_item_count, {resolution: distinct_cell_count}).
    """
    qs = MediaItem.objects.filter(_pending_query(recalculate))
    sets: dict[int, set] = {r: set() for r in resolutions}
    total = 0
    for it in qs.iterator():
        total += 1
        lat, lon = float(it.latitude), float(it.longitude)
        for r in resolutions:
            sets[r].add(h3.latlng_to_cell(lat, lon, r))
    return total, {r: len(s) for r, s in sets.items()}


@dataclass
class GeocodeStats:
    total_items: int = 0
    processed: int = 0
    skipped: int = 0
    api_calls: int = 0
    errors: int = 0
    error_details: list[str] = field(default_factory=list)


class Geocoder:
    def __init__(self, api_key: str | None = None, progress_callback=None):
        self.api_key = api_key or getattr(settings, "GOOGLE_MAPS_API_KEY", None)
        if not self.api_key:
            raise ValueError(
                "Google Maps API key required. Set GOOGLE_MAPS_API_KEY or pass api_key."
            )
        import googlemaps  # lazy: only needed when actually geocoding

        self.client = googlemaps.Client(key=self.api_key)
        self.progress = progress_callback or (lambda _m: None)

    # --- single lookup (also used by the manual-geotag UI later) ---------
    def reverse_geocode(self, lat: float, lon: float) -> Optional[dict]:
        """Return {'formatted_address', 'country_code'} for a point, or None."""
        try:
            # googlemaps attaches API methods dynamically, so static analysis
            # can't see reverse_geocode.
            results = self.client.reverse_geocode((lat, lon), language="en")  # ty: ignore[unresolved-attribute]
        except Exception as e:
            logger.error("Reverse geocode failed for (%s, %s): %s", lat, lon, e)
            return None

        if not results or not isinstance(results, list):
            return None
        first = results[0]
        if not isinstance(first, dict):
            return None

        country_code = None
        for comp in first.get("address_components", []):
            if "country" in comp.get("types", []):
                country_code = comp.get("short_name")
                break

        return {
            "formatted_address": first.get("formatted_address", ""),
            "country_code": country_code,
        }

    # --- batched bulk geocoding -----------------------------------------
    def geocode_items(
        self,
        resolution: int = DEFAULT_GEOCODE_RESOLUTION,
        recalculate: bool = False,
        max_api_calls: int | None = None,
    ) -> GeocodeStats:
        stats = GeocodeStats()

        qs = MediaItem.objects.filter(_pending_query(recalculate))
        stats.total_items = qs.count()
        if stats.total_items == 0:
            self.progress("No items to geocode")
            return stats

        groups = self._group_by_cell(qs, resolution)
        self.progress(
            f"Geocoding {stats.total_items} items in {len(groups)} H3 cells "
            f"(resolution {resolution})"
        )

        for cell, items in groups.items():
            if max_api_calls is not None and stats.api_calls >= max_api_calls:
                self.progress(f"Reached API call limit ({max_api_calls}); stopping.")
                break

            lat, lon = h3.cell_to_latlng(cell)
            try:
                info = self.reverse_geocode(lat, lon)
                stats.api_calls += 1
            except Exception as e:
                stats.errors += 1
                stats.error_details.append(f"cell {cell}: {e}")
                continue

            if not info:
                stats.skipped += len(items)
                continue

            self._apply(items, info)
            stats.processed += len(items)

            if stats.api_calls % 10 == 0:
                self.progress(f"{stats.processed}/{stats.total_items} ({stats.api_calls} calls)")

        return stats

    def _group_by_cell(self, qs, resolution: int) -> dict[str, list[MediaItem]]:
        groups: dict[str, list[MediaItem]] = defaultdict(list)
        for item in qs.iterator():
            cell = h3.latlng_to_cell(float(item.latitude), float(item.longitude), resolution)
            groups[cell].append(item)
        return dict(groups)

    def _apply(self, items: list[MediaItem], info: dict) -> None:
        now = dj_tz.now()
        label = (info.get("formatted_address") or "")[:255]
        country = (info.get("country_code") or "")[:2] or None
        for item in items:
            item.place_label = label
            item.country_code = country
            item.geocoded_at = now
        MediaItem.objects.bulk_update(items, ["place_label", "country_code", "geocoded_at"])
