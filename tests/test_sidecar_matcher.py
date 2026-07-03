"""Tests for the image -> sidecar matcher across Takeout naming quirks."""

import json

from track_me.ingest.matcher import SidecarMatcher


def _write(tmp_path, name, title=None, **extra) -> str:
    """Create a sidecar json (with optional title); return its filename/key."""
    payload = {}
    if title is not None:
        payload["title"] = title
    payload.update(extra)
    (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")
    return name


def _touch(tmp_path, name) -> str:
    (tmp_path / name).write_bytes(b"\xff\xd8\xff")  # tiny jpeg-ish stub
    return name


def _matcher(tmp_path) -> SidecarMatcher:
    # Built after files exist (it lists the tree up front).
    return SidecarMatcher.for_local(tmp_path)


def test_classic_naming(tmp_path):
    img = _touch(tmp_path, "IMG_1234.JPG")
    sc = _write(tmp_path, "IMG_1234.JPG.json", title="IMG_1234.JPG")
    assert _matcher(tmp_path).find(img) == sc


def test_supplemental_metadata(tmp_path):
    img = _touch(tmp_path, "IMG_2.JPG")
    sc = _write(tmp_path, "IMG_2.JPG.supplemental-metadata.json", title="IMG_2.JPG")
    assert _matcher(tmp_path).find(img) == sc


def test_case_insensitive_extension(tmp_path):
    img = _touch(tmp_path, "photo.jpg")
    sc = _write(tmp_path, "photo.JPG.json", title="photo.JPG")
    assert _matcher(tmp_path).find(img) == sc


def test_edited_reuses_base_sidecar(tmp_path):
    _touch(tmp_path, "IMG_9.JPG")
    sc = _write(tmp_path, "IMG_9.JPG.json", title="IMG_9.JPG")
    edited = _touch(tmp_path, "IMG_9-edited.JPG")
    assert _matcher(tmp_path).find(edited) == sc


def test_localized_edited_suffix(tmp_path):
    sc = _write(tmp_path, "DSC.JPG.json", title="DSC.JPG")
    edited = _touch(tmp_path, "DSC-bearbeitet.JPG")
    assert _matcher(tmp_path).find(edited) == sc


def test_duplicate_counter_disambiguation(tmp_path):
    img0 = _touch(tmp_path, "foo.jpg")
    img1 = _touch(tmp_path, "foo(1).jpg")
    sc0 = _write(tmp_path, "foo.jpg.json", title="foo.jpg")
    sc1 = _write(tmp_path, "foo.jpg(1).json", title="foo.jpg")
    m = _matcher(tmp_path)
    assert m.find(img0) == sc0
    assert m.find(img1) == sc1


def test_counter_with_supplemental(tmp_path):
    img1 = _touch(tmp_path, "bar(2).jpg")
    sc = _write(tmp_path, "bar.jpg.supplemental-metadata(2).json", title="bar.jpg")
    assert _matcher(tmp_path).find(img1) == sc


def test_truncated_filename_via_title(tmp_path):
    long_name = "this_is_a_really_long_photo_filename_2008_summer_trip.jpg"
    img = _touch(tmp_path, long_name)
    # Sidecar filename truncated, but the title inside is intact.
    sc = _write(tmp_path, "this_is_a_really_long_photo_filename_2008_su.json", title=long_name)
    assert _matcher(tmp_path).find(img) == sc


def test_truncated_filename_via_prefix_when_no_title(tmp_path):
    long_name = "vacation_beach_sunset_panorama_wide_2011_0042.jpg"
    img = _touch(tmp_path, long_name)
    sc = _write(tmp_path, "vacation_beach_sunset_panorama_wide_2011.json")  # no title
    assert _matcher(tmp_path).find(img) == sc


def test_no_sidecar_returns_none(tmp_path):
    img = _touch(tmp_path, "orphan.JPG")
    assert _matcher(tmp_path).find(img) is None


def test_ambiguous_prefix_refuses_to_guess(tmp_path):
    # Two unrelated images share a long prefix; a single truncated, title-less
    # sidecar must not be force-matched to either.
    _touch(tmp_path, "trip_2009_part_one_morning.jpg")
    img2 = _touch(tmp_path, "trip_2009_part_two_evening.jpg")
    _write(tmp_path, "trip_2009_part.json")  # title-less, prefix of both
    assert _matcher(tmp_path).find(img2) is None
