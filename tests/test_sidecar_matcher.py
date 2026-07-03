"""Tests for the image -> sidecar matcher across Takeout naming quirks."""

import json
from pathlib import Path

import pytest

from track_me.ingest.matcher import SidecarMatcher


def _write(path: Path, title: str | None = None, **extra) -> Path:
    """Create a sidecar json (with optional title) and return its path."""
    payload = {}
    if title is not None:
        payload["title"] = title
    payload.update(extra)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _touch(path: Path) -> Path:
    path.write_bytes(b"\xff\xd8\xff")  # tiny jpeg-ish stub
    return path


@pytest.fixture
def matcher() -> SidecarMatcher:
    return SidecarMatcher()


def test_classic_naming(tmp_path, matcher):
    img = _touch(tmp_path / "IMG_1234.JPG")
    sc = _write(tmp_path / "IMG_1234.JPG.json", title="IMG_1234.JPG")
    assert matcher.find(img) == sc


def test_supplemental_metadata(tmp_path, matcher):
    img = _touch(tmp_path / "IMG_2.JPG")
    sc = _write(tmp_path / "IMG_2.JPG.supplemental-metadata.json", title="IMG_2.JPG")
    assert matcher.find(img) == sc


def test_case_insensitive_extension(tmp_path, matcher):
    img = _touch(tmp_path / "photo.jpg")
    sc = _write(tmp_path / "photo.JPG.json", title="photo.JPG")
    assert matcher.find(img) == sc


def test_edited_reuses_base_sidecar(tmp_path, matcher):
    _touch(tmp_path / "IMG_9.JPG")
    sc = _write(tmp_path / "IMG_9.JPG.json", title="IMG_9.JPG")
    edited = _touch(tmp_path / "IMG_9-edited.JPG")
    assert matcher.find(edited) == sc


def test_localized_edited_suffix(tmp_path, matcher):
    sc = _write(tmp_path / "DSC.JPG.json", title="DSC.JPG")
    edited = _touch(tmp_path / "DSC-bearbeitet.JPG")
    assert matcher.find(edited) == sc


def test_duplicate_counter_disambiguation(tmp_path, matcher):
    img0 = _touch(tmp_path / "foo.jpg")
    img1 = _touch(tmp_path / "foo(1).jpg")
    sc0 = _write(tmp_path / "foo.jpg.json", title="foo.jpg")
    sc1 = _write(tmp_path / "foo.jpg(1).json", title="foo.jpg")
    assert matcher.find(img0) == sc0
    assert matcher.find(img1) == sc1


def test_counter_with_supplemental(tmp_path, matcher):
    img1 = _touch(tmp_path / "bar(2).jpg")
    sc = _write(tmp_path / "bar.jpg.supplemental-metadata(2).json", title="bar.jpg")
    assert matcher.find(img1) == sc


def test_truncated_filename_via_title(tmp_path, matcher):
    long_name = "this_is_a_really_long_photo_filename_2008_summer_trip.jpg"
    img = _touch(tmp_path / long_name)
    # Sidecar filename truncated, but the title inside is intact.
    sc = _write(
        tmp_path / "this_is_a_really_long_photo_filename_2008_su.json",
        title=long_name,
    )
    assert matcher.find(img) == sc


def test_truncated_filename_via_prefix_when_no_title(tmp_path, matcher):
    long_name = "vacation_beach_sunset_panorama_wide_2011_0042.jpg"
    img = _touch(tmp_path / long_name)
    sc = _write(tmp_path / "vacation_beach_sunset_panorama_wide_2011.json")  # no title
    assert matcher.find(img) == sc


def test_no_sidecar_returns_none(tmp_path, matcher):
    img = _touch(tmp_path / "orphan.JPG")
    assert matcher.find(img) is None


def test_ambiguous_prefix_refuses_to_guess(tmp_path, matcher):
    # Two unrelated images share a long prefix; a single truncated, title-less
    # sidecar must not be force-matched to either.
    _touch(tmp_path / "trip_2009_part_one_morning.jpg")
    img2 = _touch(tmp_path / "trip_2009_part_two_evening.jpg")
    _write(tmp_path / "trip_2009_part.json")  # title-less, prefix of both
    assert matcher.find(img2) is None
