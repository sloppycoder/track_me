"""EXIF + GPS + perceptual-hash extraction from an image file.

Ported from the legacy ``photo_processing_service`` (the DMS parsing, GPS IFD
handling, JSON sanitizing and hashing were hard-won) and reshaped into plain
functions returning a typed result, with no model/DB coupling.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from io import BytesIO

import imagehash
import pillow_heif
from PIL import Image
from PIL.ExifTags import GPSTAGS, TAGS

# HEIC support for the whole ingest path.
pillow_heif.register_heif_opener()

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tiff", ".gif", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".3gp", ".avi", ".mkv", ".webm"}


@dataclass
class ExifData:
    """Everything we extract from an image file's bytes."""

    meta: dict = field(default_factory=dict)
    coords: tuple[float, float] | None = None
    altitude: float | None = None
    datetime_text: str | None = None
    perceptual_hash: str | None = None


def _sanitize(text: str) -> str:
    """Strip null bytes (PostgreSQL rejects them in text/JSON)."""
    return text.replace("\x00", "")


def _jsonable(value):
    """Coerce an EXIF value into something JSON-serializable."""
    if isinstance(value, bytes):
        try:
            return _sanitize(value.decode("utf-8"))
        except UnicodeDecodeError:
            return _sanitize(str(value))
    if isinstance(value, str):
        return _sanitize(value)
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        try:
            return float(value)
        except (ValueError, ZeroDivisionError):
            return f"{value.numerator}/{value.denominator}"
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _parse_coordinate(coordinate) -> float | None:
    """EXIF GPS coordinate -> decimal degrees (handles decimal and DMS)."""
    if isinstance(coordinate, (int, float)):
        return float(coordinate)
    if isinstance(coordinate, (tuple, list)) and len(coordinate) == 3:
        d, m, s = coordinate
        return float(d) + float(m) / 60 + float(s) / 3600
    return None


def _read_meta(data: bytes) -> dict:
    """Extract EXIF tags (incl. GPS IFD) into a JSON-serializable dict."""
    try:
        with Image.open(BytesIO(data)) as img:
            exif_data = img.getexif()
    except Exception as e:
        logger.warning("Could not read EXIF: %s", e)
        return {}

    if not exif_data:
        return {}

    meta = {TAGS.get(tag_id, tag_id): _jsonable(value) for tag_id, value in exif_data.items()}

    try:
        gps_ifd = exif_data.get_ifd(0x8825)
        if gps_ifd:
            meta["GPSInfo"] = {GPSTAGS.get(k, k): _jsonable(v) for k, v in gps_ifd.items()}
    except Exception:
        pass  # GPS IFD absent or unreadable

    return meta


def _extract_gps(meta: dict) -> tuple[tuple[float, float] | None, float | None]:
    """Pull (lat, lon) and altitude out of an EXIF GPSInfo block."""
    gps = meta.get("GPSInfo")
    if not isinstance(gps, dict):
        return None, None
    if "GPSLatitude" not in gps or "GPSLongitude" not in gps:
        return None, None

    lat = _parse_coordinate(gps["GPSLatitude"])
    lon = _parse_coordinate(gps["GPSLongitude"])
    if lat is None or lon is None:
        return None, None

    if gps.get("GPSLatitudeRef") == "S":
        lat = -lat
    if gps.get("GPSLongitudeRef") == "W":
        lon = -lon

    altitude = None
    alt = gps.get("GPSAltitude")
    if isinstance(alt, (int, float)):
        altitude = float(alt)

    return (lat, lon), altitude


def _datetime_text(meta: dict) -> str | None:
    for key in ("DateTimeOriginal", "DateTime"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _perceptual_hash(data: bytes) -> str | None:
    try:
        with Image.open(BytesIO(data)) as img:
            return str(imagehash.phash(img))
    except Exception as e:
        logger.warning("Could not hash image: %s", e)
        return None


def read_exif(data: bytes, *, with_hash: bool = False) -> ExifData:
    """Read EXIF metadata, GPS, capture time, and (optionally) perceptual hash
    from raw image bytes. ``with_hash`` forces a full decode, so leave it off
    unless the hash is actually needed for identity."""
    meta = _read_meta(data)
    coords, altitude = _extract_gps(meta)
    return ExifData(
        meta=meta,
        coords=coords,
        altitude=altitude,
        datetime_text=_datetime_text(meta),
        perceptual_hash=_perceptual_hash(data) if with_hash else None,
    )
