"""Regression tests against anonymized real Takeout sidecars.

Fixtures in tests/fixtures/sidecars/ are real Google Takeout sidecars from a
2003 export (PII scrubbed: url/people/description), plus one fuzzed located
example. tests/fixtures/truncated/ reproduces the dominant real-world case where
Google truncates the whole sidecar filename to 51 chars but the JSON `title`
stays intact.
"""

from pathlib import Path

import pytest

from track_me.ingest.matcher import SidecarMatcher
from track_me.ingest.sidecar import load_sidecar

FIXTURES = Path(__file__).parent / "fixtures"
SIDECARS = FIXTURES / "sidecars"
TRUNCATED = FIXTURES / "truncated"


def test_all_real_sidecars_parse():
    files = sorted(SIDECARS.glob("*.json"))
    assert files, "no sidecar fixtures found"
    for path in files:
        sc = load_sidecar(path)
        assert sc is not None, f"failed to parse {path.name}"
        # Every real sidecar carries an authoritative timestamp and a deep link.
        assert sc.taken_epoch() is not None, f"no timestamp in {path.name}"
        assert sc.url and "photos.google.com" in sc.url


def test_2003_sidecars_have_no_location():
    # 2003 Nikon Coolpix predates GPS; geoData is 0,0 -> treated as no location.
    for path in SIDECARS.glob("DSCN*.json"):
        sc = load_sidecar(path)
        assert sc is not None
        assert sc.coords() is None, f"{path.name} should have no coords"


def test_located_example_has_coords():
    sc = load_sidecar(SIDECARS / "LOCATED_EXAMPLE.jpg.supplemental-metadata.json")
    assert sc is not None
    coords = sc.coords()
    assert coords is not None
    lat, lon = coords
    assert -90 <= lat <= 90 and -180 <= lon <= 180


def test_truncated_sidecar_matched_via_title():
    """The 51-char truncated case (the norm in real exports) must still match."""
    matcher = SidecarMatcher.for_local(TRUNCATED)
    found = matcher.find("PXL_20250102_042225386-COLLAGE.jpg")
    assert found == "PXL_20250102_042225386-COLLAGE.jpg.supplementa.json"
    # And it parses to a usable location.
    sc = load_sidecar(TRUNCATED / found)
    assert sc is not None and sc.coords() is not None


@pytest.mark.parametrize("name", ["DSCN0472.JPG", "DSCN0473.JPG", "DSCN0474.JPG"])
def test_full_supplemental_naming_matches(name):
    # The 2003 fixtures use full `.supplemental-metadata.json` naming.
    matcher = SidecarMatcher.for_local(SIDECARS)
    found = matcher.find(name)
    assert found == f"{name}.supplemental-metadata.json"
