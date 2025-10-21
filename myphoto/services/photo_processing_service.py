"""
Service for processing photo files directly.
Extracts EXIF metadata, calculates GPS, H3 indexes, and perceptual hashes.
"""

import logging
import os
from decimal import Decimal
from pathlib import Path
from typing import Optional

import imagehash
from PIL import Image
from PIL.ExifTags import TAGS

from myphoto.models import Photo

logger = logging.getLogger(__name__)

# Supported image file extensions
IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".heic",
    ".webp",
    ".psd",
}


class PhotoProcessingService:
    """Service to process photo files and extract all metadata."""

    def __init__(self, progress_callback=None):
        """
        Initialize the photo processing service.

        Args:
            progress_callback: Optional function(message: str) to report progress
        """
        self.progress_callback = progress_callback or (lambda x: None)

    def process_directory(self, directory_path: str, force_reprocess: bool = False) -> dict:
        """
        Process all photos in a directory recursively.

        Args:
            directory_path: Top-level directory containing photos
            force_reprocess: If True, reprocess even if already processed

        Returns:
            dict with statistics
        """
        stats = {
            "total_files": 0,
            "processed": 0,
            "skipped": 0,
            "updated": 0,
            "created": 0,
            "errors": 0,
            "error_details": [],
        }

        # Discover all photo files
        photo_files = self._discover_photo_files(directory_path)
        stats["total_files"] = len(photo_files)

        self.progress_callback(f"Found {stats['total_files']} photo files in {directory_path}")

        for file_path in photo_files:
            try:
                result = self.process_single_photo(file_path, directory_path, force_reprocess)

                if result["action"] == "skipped":
                    stats["skipped"] += 1
                elif result["action"] == "created":
                    stats["created"] += 1
                    stats["processed"] += 1
                elif result["action"] == "updated":
                    stats["updated"] += 1
                    stats["processed"] += 1

                # Progress update every 10 files
                if (stats["processed"] + stats["skipped"]) % 10 == 0:
                    total = stats["processed"] + stats["skipped"]
                    self.progress_callback(f"Progress: {total}/{stats['total_files']} files")

            except Exception as e:
                error_msg = f"Error processing {file_path}: {e}"
                logger.error(error_msg)
                stats["errors"] += 1
                stats["error_details"].append(error_msg)

        return stats

    def _discover_photo_files(self, directory_path: str) -> list:
        """
        Recursively discover all photo files in directory.

        Args:
            directory_path: Top-level directory

        Returns:
            List of absolute file paths
        """
        photo_files = []

        for root, _, files in os.walk(directory_path):
            for filename in files:
                ext = Path(filename).suffix.lower()
                if ext in IMAGE_EXTENSIONS:
                    abs_path = os.path.join(root, filename)
                    photo_files.append(abs_path)

        return sorted(photo_files)

    def process_single_photo(
        self,
        file_path: str,
        base_directory: str,
        force_reprocess: bool = False,
    ) -> dict:
        """
        Process a single photo file.

        Args:
            file_path: Absolute path to photo file
            base_directory: Base directory (for calculating relative path)
            force_reprocess: If True, reprocess even if already done

        Returns:
            dict with action taken ('created', 'updated', 'skipped')
        """
        # Calculate relative path
        relative_path = os.path.relpath(file_path, base_directory)

        # Check if photo already exists in database
        try:
            photo = Photo.objects.get(source_file=relative_path)
            is_new = False
        except Photo.DoesNotExist:
            photo = Photo(source_file=relative_path)
            is_new = True

        # Check if we should skip processing
        if not force_reprocess and not is_new:
            if self._is_fully_processed(photo):
                return {"action": "skipped", "photo": photo}

        # Extract all metadata and process
        self._extract_basic_info(photo, file_path, relative_path)
        self._extract_exif_metadata(photo, file_path)
        self._extract_gps_coordinates(photo)
        self._calculate_h3_indexes(photo)
        self._calculate_perceptual_hash(photo, file_path)

        # Reset geocoding when reprocessing (GPS may have changed)
        if force_reprocess and photo.has_gps:
            photo.geo_coded_at = None
            photo.location = None
            photo.country_code = None

        # Save to database
        photo.save()

        action = "created" if is_new else "updated"
        return {"action": action, "photo": photo}

    def _is_fully_processed(self, photo: Photo) -> bool:
        """
        Check if photo is fully processed.

        Returns True if:
        - Has GPS coordinates AND H3 index AND perceptual hash
        OR
        - No GPS coordinates (can't calculate H3) but has perceptual hash
        """
        has_hash = photo.perceptual_hash is not None

        if photo.has_gps:
            # If has GPS, must have H3 index and hash
            return photo.has_h3_indexes and has_hash
        else:
            # If no GPS, just need hash
            return has_hash

    def _extract_basic_info(self, photo: Photo, file_path: str, relative_path: str):
        """Extract basic file information."""
        photo.file_name = os.path.basename(file_path)
        photo.directory = os.path.dirname(relative_path)

    def _extract_exif_metadata(self, photo: Photo, file_path: str):
        """
        Extract all EXIF metadata from photo file.

        Args:
            photo: Photo model instance
            file_path: Absolute path to image file
        """
        try:
            img = Image.open(file_path)
            exif_data = img.getexif()

            if exif_data:
                # Convert EXIF data to readable format
                exif_dict = {}
                for tag_id, value in exif_data.items():
                    tag_name = TAGS.get(tag_id, tag_id)
                    # Convert bytes to string for JSON serialization
                    if isinstance(value, bytes):
                        try:
                            value = value.decode("utf-8")
                        except UnicodeDecodeError:
                            value = str(value)
                    exif_dict[tag_name] = value

                photo.exif_meta = exif_dict

                # Extract DateTime if available
                if "DateTime" in exif_dict:
                    photo.date_time_original_text = exif_dict["DateTime"]
                elif "DateTimeOriginal" in exif_dict:
                    photo.date_time_original_text = exif_dict["DateTimeOriginal"]

        except Exception as e:
            logger.warning(f"Could not extract EXIF from {file_path}: {e}")
            photo.exif_meta = {}

    def _extract_gps_coordinates(self, photo: Photo):
        """
        Extract GPS coordinates from EXIF metadata.

        Args:
            photo: Photo model instance (with exif_meta already populated)
        """
        if not photo.exif_meta:
            return

        try:
            # PIL stores GPS info in GPSInfo tag
            exif_data = photo.exif_meta

            # Check if GPS data exists
            gps_info = exif_data.get("GPSInfo")
            if not gps_info:
                return

            # GPS coordinates are stored in specific tags
            # This is a simplified version - actual GPS parsing is complex
            # You may need to use a library like piexif or exif for better GPS parsing

            # For now, try to extract from common EXIF fields
            # Many cameras store it as decimal in custom fields
            if "GPSLatitude" in exif_data and "GPSLongitude" in exif_data:
                lat = self._parse_gps_coordinate(exif_data["GPSLatitude"])
                lon = self._parse_gps_coordinate(exif_data["GPSLongitude"])

                if lat is not None and lon is not None:
                    photo.gps_latitude = Decimal(str(lat))
                    photo.gps_longitude = Decimal(str(lon))

                    if "GPSAltitude" in exif_data:
                        alt = exif_data["GPSAltitude"]
                        if isinstance(alt, (int, float)):
                            photo.gps_altitude = Decimal(str(alt))

        except Exception as e:
            logger.warning(f"Could not extract GPS for {photo.file_name}: {e}")

    def _parse_gps_coordinate(self, coordinate) -> Optional[float]:
        """
        Parse GPS coordinate from EXIF format.

        GPS coordinates in EXIF can be in various formats:
        - Decimal: 37.7749
        - DMS: (37, 46, 29.64)
        """
        if isinstance(coordinate, (int, float)):
            return float(coordinate)

        if isinstance(coordinate, tuple) and len(coordinate) == 3:
            # DMS format: (degrees, minutes, seconds)
            degrees, minutes, seconds = coordinate
            return float(degrees) + float(minutes) / 60 + float(seconds) / 3600

        return None

    def _calculate_h3_indexes(self, photo: Photo):
        """
        Calculate H3 indexes if GPS coordinates exist.

        Args:
            photo: Photo model instance
        """
        if photo.has_gps:
            photo.calculate_h3_indexes()

    def _calculate_perceptual_hash(self, photo: Photo, file_path: str):
        """
        Calculate perceptual hash for duplicate detection.

        Args:
            photo: Photo model instance
            file_path: Absolute path to image file
        """
        try:
            img = Image.open(file_path)
            photo.perceptual_hash = str(imagehash.phash(img))
        except Exception as e:
            logger.warning(f"Could not calculate hash for {file_path}: {e}")
