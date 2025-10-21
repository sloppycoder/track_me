"""
Background task wrappers for django-q2 or celery.
These functions can be called from django-q2 async tasks or celery workers.
"""

import logging

from myphoto.services.geocoding_service import GeocodingService
from myphoto.services.photo_processing_service import PhotoProcessingService

logger = logging.getLogger(__name__)


def process_photos_task(directory_path: str, force_reprocess: bool = False) -> dict:
    """
    Background task to process photos from a directory.

    This will:
    - Extract EXIF metadata
    - Extract GPS coordinates
    - Calculate H3 spatial indexes
    - Calculate perceptual hashes

    Usage with django-q2:
        from django_q.tasks import async_task
        async_task('myphoto.tasks.process_photos_task', '/path/to/photos')

    Args:
        directory_path: Top-level directory containing photo files
        force_reprocess: If True, reprocess even if already processed

    Returns:
        dict with statistics
    """
    logger.info(f"Starting photo processing task for {directory_path}")

    # Create service with logger callback
    def log_progress(message: str):
        logger.info(message)

    service = PhotoProcessingService(progress_callback=log_progress)

    try:
        stats = service.process_directory(
            directory_path=directory_path,
            force_reprocess=force_reprocess,
        )

        logger.info(
            f"Processing completed: {stats['created']} created, "
            f"{stats['updated']} updated, "
            f"{stats['skipped']} skipped, "
            f"{stats['errors']} errors"
        )

        return stats

    except Exception as e:
        logger.error(f"Photo processing task failed: {e}")
        raise


def geocode_photos_task(h3_resolution: int = 9, recalculate: bool = False) -> dict:
    """
    Background task to geocode photos using Google Maps API.

    Groups photos by H3 spatial index to minimize API calls.

    Usage with django-q2:
        from django_q.tasks import async_task
        async_task('myphoto.tasks.geocode_photos_task', h3_resolution=9)

    Args:
        h3_resolution: H3 resolution for grouping (9=~11km², 12=~0.3km²)
        recalculate: If True, recalculate even if already geocoded

    Returns:
        dict with statistics
    """
    logger.info(f"Starting geocoding task (H3 resolution {h3_resolution})")

    # Create service with logger callback
    def log_progress(message: str):
        logger.info(message)

    try:
        service = GeocodingService(progress_callback=log_progress)

        stats = service.geocode_photos(
            h3_resolution=h3_resolution,
            recalculate=recalculate,
        )

        logger.info(
            f"Geocoding completed: {stats['processed_photos']} photos, "
            f"{stats['api_calls']} API calls, "
            f"{stats['errors']} errors"
        )

        return stats

    except Exception as e:
        logger.error(f"Geocoding task failed: {e}")
        raise
