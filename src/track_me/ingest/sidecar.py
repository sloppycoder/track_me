"""Parse Google Takeout photo sidecar JSON.

The sidecar is the authoritative source for timestamp + location + the deep link
back to Google Photos. Field names have been stable for years, but exports vary,
so the schema is deliberately lenient (unknown keys ignored, everything optional).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)


class GeoData(BaseModel):
    """A geoData / geoDataExif block. Exact (0.0, 0.0) means "no location"."""

    model_config = ConfigDict(extra="ignore")

    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0

    def coords(self) -> tuple[float, float] | None:
        # Takeout writes 0.0/0.0 (not null) when there is no location. Treat the
        # exact origin as "missing" rather than a point in the Gulf of Guinea.
        if self.latitude == 0.0 and self.longitude == 0.0:
            return None
        return (self.latitude, self.longitude)


class TimeStamp(BaseModel):
    """A photoTakenTime / creationTime block: epoch seconds as a string."""

    model_config = ConfigDict(extra="ignore")

    timestamp: str | int | None = None
    formatted: str | None = None

    def epoch(self) -> int | None:
        if self.timestamp is None:
            return None
        try:
            return int(self.timestamp)
        except (TypeError, ValueError):
            return None


class Sidecar(BaseModel):
    """A parsed Takeout sidecar. All fields optional for robustness."""

    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    description: str | None = None
    url: str | None = None
    photoTakenTime: TimeStamp | None = None
    creationTime: TimeStamp | None = None
    geoData: GeoData | None = None
    geoDataExif: GeoData | None = None

    # --- convenience accessors ------------------------------------------
    def taken_epoch(self) -> int | None:
        """Authoritative capture time (epoch UTC), falling back to creationTime."""
        for ts in (self.photoTakenTime, self.creationTime):
            if ts is not None and (epoch := ts.epoch()) is not None:
                return epoch
        return None

    def coords(self) -> tuple[float, float] | None:
        """Best available location: Google's geoData, then the camera's EXIF copy."""
        for geo in (self.geoData, self.geoDataExif):
            if geo is not None and (c := geo.coords()) is not None:
                return c
        return None


def parse_sidecar(raw: dict | None) -> Sidecar | None:
    """Validate an already-parsed sidecar dict. Returns None on any failure."""
    if not isinstance(raw, dict):
        return None
    try:
        return Sidecar.model_validate(raw)
    except Exception as e:  # pydantic ValidationError or unexpected shape
        logger.warning("Could not parse sidecar: %s", e)
        return None


def load_sidecar(path: Path) -> Sidecar | None:
    """Read and validate a sidecar JSON file. Returns None on any failure."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("Could not read sidecar %s: %s", path, e)
        return None
    return parse_sidecar(raw)
