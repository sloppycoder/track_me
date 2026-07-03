"""Reverse-geocode located media into place names + country codes.

Two steps over H3-batched location cells:

  1. FETCH (Google, costs API — one call per new cell): store the cell's
     ``country_code``, ``formatted_address`` and the raw ``address_components``
     into the ``place`` table, and link each photo via ``media.geo_cell``.
  2. DERIVE (offline, free, re-runnable over stored raw): pick ``city``/``admin1``
     from the components with a priority fallback chain — no single Google field
     is "the city" worldwide.

Photos are grouped by a coarse H3 cell so each cell costs a single API call, and
the result is shared by every photo in the cell (the ``place`` row).

Note: ``taken_at`` is NOT touched here.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import h3

from track_me import config
from track_me.db import Database, Place, now_utc

logger = logging.getLogger(__name__)

# H3 resolution used to batch geocoding calls. Coarser = fewer API calls but a
# single label covers a wider area. res 9 ~ 0.1 km^2 (neighbourhood).
DEFAULT_GEOCODE_RESOLUTION = 9

# No single Google component type is "the city" worldwide; try these in order.
CITY_TYPE_PRIORITY = (
    "locality",
    "postal_town",
    "sublocality_level_1",
    "administrative_area_level_3",
    "administrative_area_level_2",
)


def _component(components: list[dict], type_name: str, key: str = "long_name") -> str | None:
    for comp in components or []:
        if type_name in comp.get("types", []):
            return comp.get(key)
    return None


def country_code_of(components: list[dict]) -> str | None:
    return _component(components, "country", "short_name")


def derive_place(components: list[dict]) -> tuple[str | None, str | None]:
    """(city, admin1) from Google address_components via the priority chain."""
    city = None
    for type_name in CITY_TYPE_PRIORITY:
        city = _component(components, type_name)
        if city:
            break
    admin1 = _component(components, "administrative_area_level_1")
    return city, admin1


def estimate_calls(
    db: Database, resolutions: list[int], recalculate: bool = False
) -> tuple[int, dict[int, int]]:
    """Distinct H3 cells (= API calls) per resolution, without any API call."""
    pending = db.media_pending_geocode(recalculate)
    sets: dict[int, set] = {r: set() for r in resolutions}
    for m in pending:
        for r in resolutions:
            cell = m.geo_cell_at(r)
            if cell:
                sets[r].add(cell)
    return len(pending), {r: len(s) for r, s in sets.items()}


@dataclass
class GeocodeStats:
    total_items: int = 0
    processed: int = 0
    skipped: int = 0
    api_calls: int = 0
    errors: int = 0
    error_details: list[str] = field(default_factory=list)


class Geocoder:
    def __init__(
        self, db: Database | None = None, api_key: str | None = None, progress_callback=None
    ):
        self.db = db or Database(config.DB_PATH)
        self.db.init_schema()
        self.api_key = api_key or config.GOOGLE_MAPS_API_KEY
        self.progress = progress_callback or (lambda _m: None)
        self._client: Any = None  # lazy: only built when a fetch actually happens

    @property
    def client(self):
        if self._client is None:
            if not self.api_key:
                raise ValueError(
                    "Google Maps API key required. Set GOOGLE_MAPS_API_KEY or pass api_key."
                )
            import googlemaps  # lazy: only needed when actually geocoding

            self._client = googlemaps.Client(key=self.api_key)
        return self._client

    # --- single lookup ---------------------------------------------------
    def reverse_geocode(self, lat: float, lon: float) -> dict | None:
        """Return {'formatted_address', 'country_code', 'components'} or None."""
        try:
            # googlemaps attaches API methods dynamically; static analysis can't see it.
            results = self.client.reverse_geocode((lat, lon), language="en")
        except Exception as e:
            logger.error("Reverse geocode failed for (%s, %s): %s", lat, lon, e)
            return None
        if not results or not isinstance(results, list):
            return None
        first = results[0]
        if not isinstance(first, dict):
            return None
        components = first.get("address_components", [])
        return {
            "formatted_address": first.get("formatted_address", ""),
            "country_code": country_code_of(components),
            "components": components,
        }

    # --- fetch (batched, costs API) --------------------------------------
    def geocode_items(
        self,
        resolution: int = DEFAULT_GEOCODE_RESOLUTION,
        recalculate: bool = False,
        max_api_calls: int | None = None,
    ) -> GeocodeStats:
        stats = GeocodeStats()
        pending = self.db.media_pending_geocode(recalculate)
        stats.total_items = len(pending)
        if not pending:
            self.progress("No items to geocode")
            return stats

        groups: dict[str, list] = defaultdict(list)
        for m in pending:
            cell = m.geo_cell_at(resolution)
            if cell:
                groups[cell].append(m)
        self.progress(
            f"Geocoding {stats.total_items} items in {len(groups)} H3 cells "
            f"(resolution {resolution})"
        )

        for cell, items in groups.items():
            existing = self.db.get_place(cell)
            if existing is None or existing.geocode_raw is None or recalculate:
                # Need an API call for this cell.
                if max_api_calls is not None and stats.api_calls >= max_api_calls:
                    self.progress(f"Reached API call limit ({max_api_calls}); stopping.")
                    break
                lat, lon = h3.cell_to_latlng(cell)
                info = self.reverse_geocode(lat, lon)
                stats.api_calls += 1
                if not info:
                    stats.skipped += len(items)
                    continue
                city, admin1 = derive_place(info["components"])
                self.db.upsert_place(
                    Place(
                        h3_cell=cell,
                        center_lat=lat,
                        center_lng=lon,
                        city=city,
                        admin1=admin1,
                        country_code=info["country_code"],
                        formatted_address=info["formatted_address"][:255],
                        geocode_raw=info["components"],
                        geocoded_at=now_utc(),
                    )
                )
            # Link every photo in the cell (place row now exists).
            self.db.set_geo_cells([(m.dedupe_key, cell) for m in items])
            stats.processed += len(items)

            if stats.api_calls and stats.api_calls % 10 == 0:
                self.progress(f"{stats.processed}/{stats.total_items} ({stats.api_calls} calls)")

        return stats

    # --- derive (offline, free) ------------------------------------------
    def derive_all(self, redo: bool = False) -> int:
        """Recompute city/admin1 from stored geocode_raw. Returns rows updated."""
        places = self.db.places_pending_derive(redo)
        for p in places:
            raw = p.geocode_raw if isinstance(p.geocode_raw, list) else []
            city, admin1 = derive_place(raw)
            self.db.update_place_derived(p.h3_cell, city, admin1)
        return len(places)
