"""Unit tests for sidecar JSON parsing."""

import json

from library.ingest.sidecar import Sidecar, load_sidecar


def test_parse_full_sidecar():
    sc = Sidecar.model_validate(
        {
            "title": "IMG_1.JPG",
            "url": "https://photos.google.com/photo/ABC",
            "photoTakenTime": {"timestamp": "1564920000", "formatted": "..."},
            "geoData": {"latitude": 35.6895, "longitude": 139.6917, "altitude": 40.0},
            "junk_field": "ignored",
        }
    )
    assert sc.taken_epoch() == 1564920000
    assert sc.coords() == (35.6895, 139.6917)
    assert sc.url == "https://photos.google.com/photo/ABC"


def test_zero_geodata_is_no_location():
    sc = Sidecar.model_validate({"geoData": {"latitude": 0.0, "longitude": 0.0}})
    assert sc.coords() is None


def test_falls_back_to_geodata_exif():
    sc = Sidecar.model_validate(
        {
            "geoData": {"latitude": 0.0, "longitude": 0.0},
            "geoDataExif": {"latitude": 1.29, "longitude": 103.85},
        }
    )
    assert sc.coords() == (1.29, 103.85)


def test_falls_back_to_creation_time():
    sc = Sidecar.model_validate({"creationTime": {"timestamp": "100"}})
    assert sc.taken_epoch() == 100


def test_missing_everything():
    sc = Sidecar.model_validate({})
    assert sc.taken_epoch() is None
    assert sc.coords() is None


def test_load_sidecar_bad_json(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_sidecar(bad) is None


def test_load_sidecar_ok(tmp_path):
    good = tmp_path / "g.json"
    good.write_text(json.dumps({"title": "x.jpg"}), encoding="utf-8")
    sc = load_sidecar(good)
    assert sc is not None and sc.title == "x.jpg"
