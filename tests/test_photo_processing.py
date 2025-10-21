"""
Tests for photo processing service using real test photos.
"""

import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from myphoto.models import Photo
from myphoto.services.photo_processing_service import PhotoProcessingService

# Test photos directory
TEST_PHOTOS_DIR = Path(__file__).parent / "test_photos"

# Test photos with GPS coordinates (verified via EXIF inspection)
# Format: {relative_path: {expected_data}}
PHOTOS_WITH_GPS = {
    "2025-02/PXL_20250204_030635106.jpg": {
        "lat": Decimal("35.703335"),  # 35°42'12.06"N
        "lon": Decimal("139.774197"),  # 139°46'27.11"E
        "make": "Google",
        "model": "Pixel 9 Pro Fold",
    },
    "2015-01/IMG_4756.JPG": {
        "lat": Decimal("10.771247"),  # 10°46'16.49"N
        "lon": Decimal("106.693597"),  # 106°41'36.95"E
        "make": "Apple",
        "model": "iPhone 5s",
    },
    "2025-02/PXL_20250217_025515106.jpg": {
        "lat": Decimal("34.665206"),  # 34°39'54.74"N
        "lon": Decimal("135.501572"),  # 135°30'5.66"E
        "make": "Google",
        "model": "Pixel 9 Pro Fold",
    },
    "2025-02/PXL_20250224_104645452.jpg": {
        "lat": Decimal("33.589136"),  # 33°35'20.89"N
        "lon": Decimal("130.396217"),  # 130°23'46.38"E
        "make": "Google",
        "model": "Pixel 9 Pro Fold",
    },
}

# Test photos without GPS (relative paths from TEST_PHOTOS_DIR)
PHOTOS_WITHOUT_GPS = [
    "2006-12/IMG_0243.JPG",  # Canon camera, no GPS
    "2015-01/IMG_4725.PNG",  # Screenshot, no GPS
    "2020-04/IMG_3247.JPG",  # iPhone 11, no GPS (originally HEIC)
    "date-unknown/Screenshot_20250225-121230.png",  # Screenshot, no EXIF
]


@pytest.mark.django_db
class TestPhotoProcessingService:
    """Test the PhotoProcessingService with real test photos."""

    def test_discover_photo_files(self, tmp_path):
        """Test discovering photo files in directory."""
        # Copy test photos to tmp directory
        for photo_path in ["2025-02/PXL_20250204_030635106.jpg", "2015-01/IMG_4756.JPG"]:
            src = TEST_PHOTOS_DIR / photo_path
            dst = tmp_path / Path(photo_path).name
            shutil.copy2(src, dst)

        # Create subdirectory with more photos
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        for photo_path in ["2006-12/IMG_0243.JPG", "2015-01/IMG_4725.PNG"]:
            src = TEST_PHOTOS_DIR / photo_path
            dst = subdir / Path(photo_path).name
            shutil.copy2(src, dst)

        # Create non-photo file
        (tmp_path / "ignored.txt").write_text("ignored")

        service = PhotoProcessingService()
        files = service._discover_photo_files(str(tmp_path))

        assert len(files) == 4
        assert all(f.endswith((".jpg", ".JPG", ".PNG", ".png")) for f in files)

    def test_process_photo_with_gps(self):
        """Test processing photo with GPS coordinates."""
        # Use photo with known GPS coordinates
        photo_rel_path = "2025-02/PXL_20250204_030635106.jpg"
        expected = PHOTOS_WITH_GPS[photo_rel_path]

        photo_path = TEST_PHOTOS_DIR / photo_rel_path

        service = PhotoProcessingService()
        result = service.process_single_photo(
            str(photo_path),
            str(TEST_PHOTOS_DIR),
            force_reprocess=False,
        )

        assert result["action"] == "created"
        photo = result["photo"]

        # Verify basic info
        assert photo.file_name == Path(photo_rel_path).name
        assert photo.source_file == photo_rel_path

        # Verify GPS coordinates (with tolerance for DMS conversion)
        assert photo.gps_latitude is not None
        assert photo.gps_longitude is not None
        assert abs(photo.gps_latitude - expected["lat"]) < Decimal("0.0001")
        assert abs(photo.gps_longitude - expected["lon"]) < Decimal("0.0001")

        # Verify camera info in EXIF metadata
        assert photo.exif_meta is not None
        assert photo.exif_meta.get("Make") == expected["make"]
        assert photo.exif_meta.get("Model") == expected["model"]

        # Verify H3 indexes were calculated
        assert photo.h3_res_3 is not None
        assert photo.h3_res_6 is not None
        assert photo.h3_res_9 is not None
        assert photo.h3_res_12 is not None
        assert photo.h3_res_15 is not None

        # Verify perceptual hash
        assert photo.perceptual_hash is not None
        assert len(photo.perceptual_hash) == 16  # 64-bit hash = 16 hex chars

    def test_process_photo_without_gps(self):
        """Test processing photo without GPS coordinates."""
        photo_rel_path = "2006-12/IMG_0243.JPG"
        photo_path = TEST_PHOTOS_DIR / photo_rel_path

        service = PhotoProcessingService()
        result = service.process_single_photo(
            str(photo_path),
            str(TEST_PHOTOS_DIR),
            force_reprocess=False,
        )

        assert result["action"] == "created"
        photo = result["photo"]

        # Verify basic info
        assert photo.file_name == Path(photo_rel_path).name
        assert photo.source_file == photo_rel_path

        # Verify NO GPS coordinates
        assert photo.gps_latitude is None
        assert photo.gps_longitude is None

        # Verify NO H3 indexes
        assert photo.h3_res_3 is None
        assert photo.h3_res_6 is None
        assert photo.h3_res_9 is None
        assert photo.h3_res_12 is None
        assert photo.h3_res_15 is None

        # Verify camera info in EXIF metadata
        assert photo.exif_meta is not None
        assert photo.exif_meta.get("Make") == "Canon"
        assert photo.exif_meta.get("Model") == "Canon DIGITAL IXUS 850 IS"

        # Verify perceptual hash still calculated
        assert photo.perceptual_hash is not None
        assert len(photo.perceptual_hash) == 16

    def test_process_multiple_photos_with_gps(self, tmp_path):
        """Test processing multiple photos with different GPS locations."""
        photo_rel_paths = [
            "2025-02/PXL_20250204_030635106.jpg",  # Tokyo area
            "2015-01/IMG_4756.JPG",  # Vietnam area
            "2025-02/PXL_20250224_104645452.jpg",  # Fukuoka area
        ]

        for photo_rel_path in photo_rel_paths:
            src = TEST_PHOTOS_DIR / photo_rel_path
            dst = tmp_path / Path(photo_rel_path).name
            shutil.copy2(src, dst)

        service = PhotoProcessingService()
        stats = service.process_directory(str(tmp_path), force_reprocess=False)

        assert stats["total_files"] == 3
        assert stats["created"] == 3
        assert stats["processed"] == 3
        assert stats["skipped"] == 0
        assert stats["errors"] == 0

        # Verify all photos in database
        assert Photo.objects.count() == 3

        # Verify each photo has GPS and H3 indexes
        for photo_rel_path in photo_rel_paths:
            photo_name = Path(photo_rel_path).name
            photo = Photo.objects.get(file_name=photo_name)
            expected = PHOTOS_WITH_GPS[photo_rel_path]

            # Check GPS
            assert photo.gps_latitude is not None
            assert photo.gps_longitude is not None
            assert abs(photo.gps_latitude - expected["lat"]) < Decimal("0.0001")
            assert abs(photo.gps_longitude - expected["lon"]) < Decimal("0.0001")

            # Check H3 indexes
            assert photo.h3_res_3 is not None
            assert photo.h3_res_12 is not None

            # Check perceptual hash
            assert photo.perceptual_hash is not None

    def test_process_mixed_photos(self, tmp_path):
        """Test processing mix of photos with and without GPS."""
        # Copy photos
        photos_to_copy = [
            "2025-02/PXL_20250204_030635106.jpg",  # Has GPS
            "2006-12/IMG_0243.JPG",  # No GPS
            "2015-01/IMG_4756.JPG",  # Has GPS
            "2015-01/IMG_4725.PNG",  # No GPS (screenshot)
        ]

        for photo_rel_path in photos_to_copy:
            src = TEST_PHOTOS_DIR / photo_rel_path
            dst = tmp_path / Path(photo_rel_path).name
            shutil.copy2(src, dst)

        service = PhotoProcessingService()
        stats = service.process_directory(str(tmp_path), force_reprocess=False)

        assert stats["total_files"] == 4
        assert stats["created"] == 4
        assert stats["processed"] == 4

        # Verify photos with GPS
        for photo_rel_path in [
            "2025-02/PXL_20250204_030635106.jpg",
            "2015-01/IMG_4756.JPG",
        ]:
            photo_name = Path(photo_rel_path).name
            photo = Photo.objects.get(file_name=photo_name)
            assert photo.gps_latitude is not None
            assert photo.gps_longitude is not None
            assert photo.h3_res_12 is not None

        # Verify photos without GPS
        for photo_rel_path in ["2006-12/IMG_0243.JPG", "2015-01/IMG_4725.PNG"]:
            photo_name = Path(photo_rel_path).name
            photo = Photo.objects.get(file_name=photo_name)
            assert photo.gps_latitude is None
            assert photo.gps_longitude is None
            assert photo.h3_res_12 is None

    def test_process_single_photo_skip_existing(self):
        """Test that processing skips already-processed photos."""
        photo_rel_path = "2025-02/PXL_20250204_030635106.jpg"
        photo_path = TEST_PHOTOS_DIR / photo_rel_path

        service = PhotoProcessingService()

        # First process - should create
        result1 = service.process_single_photo(
            str(photo_path),
            str(TEST_PHOTOS_DIR),
            force_reprocess=False,
        )
        assert result1["action"] == "created"

        # Second process - should skip (has perceptual hash)
        result2 = service.process_single_photo(
            str(photo_path),
            str(TEST_PHOTOS_DIR),
            force_reprocess=False,
        )
        assert result2["action"] == "skipped"

        # Verify only one record in database
        assert Photo.objects.count() == 1

    def test_process_single_photo_force_reprocess(self):
        """Test force reprocess updates existing record."""
        photo_rel_path = "2015-01/IMG_4756.JPG"
        photo_path = TEST_PHOTOS_DIR / photo_rel_path

        service = PhotoProcessingService()

        # First process
        result1 = service.process_single_photo(
            str(photo_path),
            str(TEST_PHOTOS_DIR),
            force_reprocess=False,
        )
        assert result1["action"] == "created"
        original_hash = result1["photo"].perceptual_hash

        # Force reprocess
        result2 = service.process_single_photo(
            str(photo_path),
            str(TEST_PHOTOS_DIR),
            force_reprocess=True,
        )
        assert result2["action"] == "updated"

        # Verify same hash (same photo)
        assert result2["photo"].perceptual_hash == original_hash

        # Verify only one record in database
        assert Photo.objects.count() == 1

    def test_extract_subdirectory_info(self, tmp_path):
        """Test extraction of directory information."""
        subdir = tmp_path / "vacation" / "2025"
        subdir.mkdir(parents=True, exist_ok=True)

        photo_rel_path = "2025-02/PXL_20250204_030635106.jpg"
        photo_name = Path(photo_rel_path).name
        src = TEST_PHOTOS_DIR / photo_rel_path
        dst = subdir / photo_name
        shutil.copy2(src, dst)

        service = PhotoProcessingService()
        result = service.process_single_photo(
            str(dst),
            str(tmp_path),
            force_reprocess=False,
        )

        photo = result["photo"]
        assert photo.file_name == photo_name
        assert photo.directory == str(Path("vacation") / "2025")
        assert photo.source_file == str(Path("vacation") / "2025" / photo_name)

    def test_perceptual_hash_uniqueness(self):
        """Test that different photos get different perceptual hashes."""
        photo_rel_paths = [
            "2025-02/PXL_20250204_030635106.jpg",
            "2015-01/IMG_4756.JPG",
            "2006-12/IMG_0243.JPG",
        ]

        service = PhotoProcessingService()
        hashes = []
        for photo_rel_path in photo_rel_paths:
            photo_path = TEST_PHOTOS_DIR / photo_rel_path
            result = service.process_single_photo(
                str(photo_path),
                str(TEST_PHOTOS_DIR),
                force_reprocess=False,
            )
            hashes.append(result["photo"].perceptual_hash)

        # All hashes should be different (different photos)
        assert len(set(hashes)) == len(hashes)

    def test_h3_indexes_different_resolutions(self):
        """Test that H3 indexes at different resolutions are calculated."""
        photo_rel_path = "2025-02/PXL_20250217_025515106.jpg"
        photo_path = TEST_PHOTOS_DIR / photo_rel_path

        service = PhotoProcessingService()
        result = service.process_single_photo(
            str(photo_path),
            str(TEST_PHOTOS_DIR),
            force_reprocess=False,
        )

        photo = result["photo"]

        # All H3 indexes should be calculated
        assert photo.h3_res_3 is not None
        assert photo.h3_res_6 is not None
        assert photo.h3_res_9 is not None
        assert photo.h3_res_12 is not None
        assert photo.h3_res_15 is not None

        # Different resolutions should give different cell IDs
        h3_values = [
            photo.h3_res_3,
            photo.h3_res_6,
            photo.h3_res_9,
            photo.h3_res_12,
            photo.h3_res_15,
        ]
        assert len(set(h3_values)) == 5  # All unique

    def test_process_screenshot_no_exif(self):
        """Test processing screenshot with no EXIF data."""
        photo_rel_path = "date-unknown/Screenshot_20250225-121230.png"
        photo_path = TEST_PHOTOS_DIR / photo_rel_path

        service = PhotoProcessingService()
        result = service.process_single_photo(
            str(photo_path),
            str(TEST_PHOTOS_DIR),
            force_reprocess=False,
        )

        assert result["action"] == "created"
        photo = result["photo"]

        # Verify basic info
        assert photo.file_name == Path(photo_rel_path).name

        # No EXIF data
        assert photo.gps_latitude is None
        assert photo.gps_longitude is None
        # Screenshot may have empty or no EXIF metadata
        if photo.exif_meta:
            assert photo.exif_meta.get("Make") is None
            assert photo.exif_meta.get("Model") is None

        # But still has perceptual hash
        assert photo.perceptual_hash is not None

    def test_process_heic_converted_photo(self):
        """Test processing HEIC-converted photo (originally HEIC, saved as JPG)."""
        photo_rel_path = "2020-04/IMG_3247.JPG"
        photo_path = TEST_PHOTOS_DIR / photo_rel_path

        service = PhotoProcessingService()
        result = service.process_single_photo(
            str(photo_path),
            str(TEST_PHOTOS_DIR),
            force_reprocess=False,
        )

        assert result["action"] == "created"
        photo = result["photo"]

        # Verify basic info
        assert photo.file_name == Path(photo_rel_path).name
        assert photo.source_file == photo_rel_path

        # Verify NO GPS coordinates
        assert photo.gps_latitude is None
        assert photo.gps_longitude is None
        assert photo.h3_res_12 is None

        # Verify EXIF metadata was extracted
        assert photo.exif_meta is not None
        assert photo.exif_meta.get("Make") == "Apple"
        assert photo.exif_meta.get("Model") == "iPhone 11"

        # DateTime should be extracted (not DateTimeOriginal for this file)
        assert photo.date_time_original_text == "2020:04:02 18:10:27"

        # Verify perceptual hash calculated
        assert photo.perceptual_hash is not None
        assert len(photo.perceptual_hash) == 16

    def test_perceptual_hash_detects_scaled_duplicate(self):
        """Test that perceptual hash detects scaled version as duplicate."""
        original_rel_path = "2006-12/IMG_0243.JPG"
        scaled_rel_path = "2006-12/IMG_0243_2x.JPG"

        original_path = TEST_PHOTOS_DIR / original_rel_path
        scaled_path = TEST_PHOTOS_DIR / scaled_rel_path

        service = PhotoProcessingService()

        # Process original photo first
        result1 = service.process_single_photo(
            str(original_path),
            str(TEST_PHOTOS_DIR),
            force_reprocess=False,
        )
        assert result1["action"] == "created"
        original_photo = result1["photo"]
        original_hash = original_photo.perceptual_hash

        # Process scaled version (2x size)
        result2 = service.process_single_photo(
            str(scaled_path),
            str(TEST_PHOTOS_DIR),
            force_reprocess=False,
        )
        assert result2["action"] == "created"
        scaled_photo = result2["photo"]
        scaled_hash = scaled_photo.perceptual_hash

        # Perceptual hashes should be IDENTICAL (same image, different size)
        assert original_hash == scaled_hash
        assert original_hash is not None
        assert scaled_hash is not None

        # Both photos should be in database (different source files)
        assert Photo.objects.count() == 2
        assert Photo.objects.filter(perceptual_hash=original_hash).count() == 2
