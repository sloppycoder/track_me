"""
Tests for photo processing service.
"""

from pathlib import Path

import pytest
from PIL import Image

from myphoto.models import Photo
from myphoto.services.photo_processing_service import PhotoProcessingService


@pytest.mark.django_db
class TestPhotoProcessingService:
    """Test the PhotoProcessingService."""

    def test_discover_photo_files(self, tmp_path):
        """Test discovering photo files in directory."""
        # Create test directory structure
        (tmp_path / "subdir1").mkdir()
        (tmp_path / "subdir2").mkdir()

        # Create test image files
        self._create_test_image(tmp_path / "photo1.jpg")
        self._create_test_image(tmp_path / "subdir1" / "photo2.jpg")
        self._create_test_image(tmp_path / "subdir2" / "photo3.png")
        (tmp_path / "ignored.txt").write_text("ignored")

        service = PhotoProcessingService()
        files = service._discover_photo_files(str(tmp_path))

        assert len(files) == 3
        assert all(f.endswith((".jpg", ".png")) for f in files)

    def test_process_single_photo_creates_record(self, tmp_path):
        """Test processing a single photo creates a database record."""
        # Create test image
        photo_path = tmp_path / "test_photo.jpg"
        self._create_test_image(photo_path)

        service = PhotoProcessingService()
        result = service.process_single_photo(
            str(photo_path),
            str(tmp_path),
            force_reprocess=False,
        )

        assert result["action"] == "created"
        assert result["photo"].file_name == "test_photo.jpg"
        assert result["photo"].source_file == "test_photo.jpg"

        # Verify in database
        photo = Photo.objects.get(source_file="test_photo.jpg")
        assert photo.file_name == "test_photo.jpg"

    def test_process_single_photo_skip_existing(self, tmp_path):
        """Test that processing skips already-processed photos."""
        # Create test image
        photo_path = tmp_path / "test_photo.jpg"
        self._create_test_image(photo_path)

        service = PhotoProcessingService()

        # First process - should create
        result1 = service.process_single_photo(
            str(photo_path),
            str(tmp_path),
            force_reprocess=False,
        )
        assert result1["action"] == "created"

        # Second process - should skip (has perceptual hash)
        result2 = service.process_single_photo(
            str(photo_path),
            str(tmp_path),
            force_reprocess=False,
        )
        assert result2["action"] == "skipped"

    def test_process_single_photo_force_reprocess(self, tmp_path):
        """Test force reprocess updates existing record."""
        # Create test image
        photo_path = tmp_path / "test_photo.jpg"
        self._create_test_image(photo_path)

        service = PhotoProcessingService()

        # First process
        result1 = service.process_single_photo(
            str(photo_path),
            str(tmp_path),
            force_reprocess=False,
        )
        assert result1["action"] == "created"

        # Force reprocess
        result2 = service.process_single_photo(
            str(photo_path),
            str(tmp_path),
            force_reprocess=True,
        )
        assert result2["action"] == "updated"

    def test_process_directory(self, tmp_path):
        """Test processing entire directory."""
        # Create multiple test images
        (tmp_path / "subdir").mkdir()
        self._create_test_image(tmp_path / "photo1.jpg")
        self._create_test_image(tmp_path / "photo2.jpg")
        self._create_test_image(tmp_path / "subdir" / "photo3.png")

        service = PhotoProcessingService()
        stats = service.process_directory(str(tmp_path), force_reprocess=False)

        assert stats["total_files"] == 3
        assert stats["created"] == 3
        assert stats["processed"] == 3
        assert stats["skipped"] == 0
        assert stats["errors"] == 0

        # Verify all in database
        assert Photo.objects.count() == 3

    def test_extract_basic_info(self, tmp_path):
        """Test extraction of basic file information."""
        photo_path = tmp_path / "subdir" / "test_photo.jpg"
        photo_path.parent.mkdir(parents=True, exist_ok=True)
        self._create_test_image(photo_path)

        service = PhotoProcessingService()
        result = service.process_single_photo(
            str(photo_path),
            str(tmp_path),
            force_reprocess=False,
        )

        photo = result["photo"]
        assert photo.file_name == "test_photo.jpg"
        assert photo.directory == "subdir"
        assert photo.source_file == str(Path("subdir") / "test_photo.jpg")

    def test_calculate_perceptual_hash(self, tmp_path):
        """Test perceptual hash calculation."""
        photo_path = tmp_path / "test_photo.jpg"
        self._create_test_image(photo_path)

        service = PhotoProcessingService()
        result = service.process_single_photo(
            str(photo_path),
            str(tmp_path),
            force_reprocess=False,
        )

        photo = result["photo"]
        assert photo.perceptual_hash is not None
        assert len(photo.perceptual_hash) == 16  # 64-bit hash = 16 hex chars

    def _create_test_image(self, path: Path, size=(100, 100)):
        """Create a simple test image."""
        path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", size, color="red")
        img.save(str(path))
