"""Unit tests for EXIF GPS parsing (no image files needed)."""

from library.ingest.exif import _extract_gps, _parse_coordinate


def test_parse_decimal_coordinate():
    assert _parse_coordinate(37.7749) == 37.7749


def test_parse_dms_coordinate():
    # 37 deg 46' 29.64" -> ~37.7749
    val = _parse_coordinate((37, 46, 29.64))
    assert val is not None
    assert abs(val - 37.7749) < 1e-3


def test_parse_invalid_coordinate():
    assert _parse_coordinate("nope") is None
    assert _parse_coordinate((1, 2)) is None


def test_extract_gps_with_hemisphere_refs():
    meta = {
        "GPSInfo": {
            "GPSLatitude": (1, 17, 0),
            "GPSLatitudeRef": "S",
            "GPSLongitude": (103, 51, 0),
            "GPSLongitudeRef": "E",
            "GPSAltitude": 15.0,
        }
    }
    coords, altitude = _extract_gps(meta)
    assert coords is not None
    lat, lon = coords
    assert lat < 0  # south -> negative
    assert lon > 0  # east -> positive
    assert altitude == 15.0


def test_extract_gps_absent():
    assert _extract_gps({}) == (None, None)
    assert _extract_gps({"GPSInfo": {"GPSLatitude": (1, 2, 3)}}) == (None, None)
