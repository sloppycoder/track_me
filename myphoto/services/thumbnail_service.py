"""
Service for generating and caching photo thumbnails.
"""

import hashlib
import logging
import os
from pathlib import Path

import pillow_heif
from PIL import Image

from myphoto.models import Photo

# Register HEIF opener for HEIC support
pillow_heif.register_heif_opener()

logger = logging.getLogger(__name__)


class ThumbnailService:
    """Service to generate and cache photo thumbnails."""

    def __init__(self, cache_dir: Path, thumbnail_size: tuple[int, int], base_dir: str):
        """
        Initialize the thumbnail service.

        Args:
            cache_dir: Directory to store cached thumbnails
            thumbnail_size: Tuple of (width, height) for thumbnails
            base_dir: Base directory where photos are stored
        """
        self.cache_dir = Path(cache_dir)
        self.thumbnail_size = thumbnail_size
        self.base_dir = Path(base_dir) if base_dir else None

        # Ensure cache directory exists
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_thumbnail_path(self, photo: Photo) -> Path:
        """
        Get the cache path for a photo's thumbnail.

        Uses photo ID and source file hash for uniqueness.

        Args:
            photo: Photo model instance

        Returns:
            Path to the cached thumbnail
        """
        # Create a hash of the source file path for uniqueness
        file_hash = hashlib.md5(photo.source_file.encode()).hexdigest()[:8]
        filename = f"{photo.id}_{file_hash}.jpg"  # type: ignore[attr-defined]
        return self.cache_dir / filename

    def generate_thumbnail(self, photo: Photo) -> Path:
        """
        Generate or retrieve cached thumbnail for a photo.

        Args:
            photo: Photo model instance

        Returns:
            Path to the thumbnail file

        Raises:
            FileNotFoundError: If source photo doesn't exist
            ValueError: If path traversal detected
        """
        thumbnail_path = self.get_thumbnail_path(photo)

        # Check if cached thumbnail exists and is fresh
        if self._is_thumbnail_fresh(photo, thumbnail_path):
            return thumbnail_path

        # Generate new thumbnail
        source_path = self._get_source_file_path(photo)

        if not source_path.exists():
            raise FileNotFoundError(f"Source photo not found: {source_path}")

        logger.debug(f"Generating thumbnail for {photo.file_name}")

        # Open and resize image
        with Image.open(source_path) as img:
            # Convert RGBA to RGB if necessary (for JPEG output)
            if img.mode in ("RGBA", "LA", "P"):
                # Create white background
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                background.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
                img = background

            # Resize maintaining aspect ratio
            img.thumbnail(self.thumbnail_size, Image.Resampling.LANCZOS)

            # Save as JPEG
            img.save(thumbnail_path, "JPEG", quality=85, optimize=True)

        logger.info(f"Thumbnail generated: {thumbnail_path}")
        return thumbnail_path

    def _is_thumbnail_fresh(self, photo: Photo, thumbnail_path: Path) -> bool:
        """
        Check if cached thumbnail exists and is up-to-date.

        Args:
            photo: Photo model instance
            thumbnail_path: Path to cached thumbnail

        Returns:
            True if thumbnail is fresh, False otherwise
        """
        if not thumbnail_path.exists():
            return False

        # Check if source file was modified after thumbnail
        try:
            source_path = self._get_source_file_path(photo)
            if not source_path.exists():
                return False

            source_mtime = source_path.stat().st_mtime
            thumbnail_mtime = thumbnail_path.stat().st_mtime

            return thumbnail_mtime >= source_mtime
        except (FileNotFoundError, ValueError):
            return False

    def _get_source_file_path(self, photo: Photo) -> Path:
        """
        Resolve the full path to the source photo file with security checks.

        Args:
            photo: Photo model instance

        Returns:
            Resolved path to source file

        Raises:
            ValueError: If path traversal detected or base_dir not configured
        """
        if not self.base_dir:
            raise ValueError("PHOTOS_BASE_DIR not configured in settings")

        # Security: Check for path traversal attempts
        source_file = photo.source_file
        if ".." in source_file or source_file.startswith(os.sep):
            raise ValueError(f"Invalid file path (path traversal detected): {source_file}")

        # Combine base dir with source file
        full_path = (self.base_dir / source_file).resolve()

        # Ensure resolved path is within base directory
        try:
            full_path.relative_to(self.base_dir.resolve())
        except ValueError:
            raise ValueError(
                f"Path traversal detected: {source_file} resolves outside base directory"
            )

        return full_path
