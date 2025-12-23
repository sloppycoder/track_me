import json
import logging
from calendar import monthrange
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import dateparser
from django.conf import settings
from django.db.models import Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from myphoto.models import Photo
from myphoto.services.geocoding_service import GeocodingService
from myphoto.services.thumbnail_service import ThumbnailService

logger = logging.getLogger(__name__)


def parse_date_filter(date_str):
    """
    Parse flexible date formats for filtering using dateparser library.

    Supports:
    - "2004" -> Jan 1 2004 to Dec 31 2004
    - "jan 2004" or "January 2004" -> Jan 1-31 2004
    - "2005-01-01" -> Exact date
    - Many other human-readable formats

    Returns:
        Tuple of (start_datetime, end_datetime) or (None, None) if invalid
    """
    if not date_str:
        return None, None

    date_str = date_str.strip()

    # Parse the date using dateparser
    parsed_date = dateparser.parse(
        date_str,
        settings={
            "PREFER_DATES_FROM": "past",
            "RELATIVE_BASE": datetime.now(),
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )

    if not parsed_date:
        return None, None

    # Handle year-only format (e.g., "2004")
    if date_str.isdigit() and len(date_str) == 4:
        year = int(date_str)
        start = timezone.make_aware(datetime(year, 1, 1))
        end = timezone.make_aware(datetime(year, 12, 31, 23, 59, 59))
        return start, end

    # Handle month+year format (e.g., "jan 2004", "January 2004")
    parts = date_str.lower().split()
    if len(parts) == 2 and parts[1].isdigit():
        # Assume it's a month+year format
        year = parsed_date.year
        month = parsed_date.month
        start = timezone.make_aware(datetime(year, month, 1))
        # Get last day of month
        last_day = monthrange(year, month)[1]
        end = timezone.make_aware(datetime(year, month, last_day, 23, 59, 59))
        return start, end

    # For specific dates, return start of day to end of day
    start = parsed_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end = parsed_date.replace(hour=23, minute=59, second=59, microsecond=999999)

    return start, end


def photo_list(request):
    """Main photo list page with search and location assignment."""
    return render(
        request,
        "myphoto/photo_list.html",
        {
            "api_key": settings.GOOGLE_MAPS_API_KEY,
            "max_photos": settings.MAX_PHOTOS_PER_PAGE,
        },
    )


@require_GET  # type: ignore[misc]
def api_photo_search(request):
    """
    API endpoint to search photos by text and date filters.

    Query params:
        q: Search text (location or country_code)
        date_from: Start date in flexible format
        date_to: End date in flexible format
        limit: Maximum results (default: MAX_PHOTOS_PER_PAGE)
    """
    query_text = request.GET.get("q", "").strip()
    date_from_str = request.GET.get("date_from", "").strip()
    date_to_str = request.GET.get("date_to", "").strip()
    limit = int(request.GET.get("limit", settings.MAX_PHOTOS_PER_PAGE))

    # Start with all photos
    queryset = Photo.objects.all()

    # Text search (case-insensitive on location or country_code)
    if query_text:
        queryset = queryset.filter(
            Q(location__icontains=query_text) | Q(country_code__icontains=query_text)
        )

    # Date filtering
    if date_from_str or date_to_str:
        start_date, _ = parse_date_filter(date_from_str)
        _, end_date = parse_date_filter(date_to_str)

        if start_date and end_date:
            # Range query
            queryset = queryset.filter(date_time_taken__range=[start_date, end_date])
        elif start_date:
            queryset = queryset.filter(date_time_taken__gte=start_date)
        elif end_date:
            queryset = queryset.filter(date_time_taken__lte=end_date)

    # Order by date (most recent first)
    queryset = queryset.order_by("-date_time_taken")

    # Limit results
    queryset = queryset[:limit]

    # Serialize to JSON
    photos = []
    for photo in queryset:
        photos.append(
            {
                "id": photo.id,  # type: ignore[attr-defined]
                "file_name": photo.file_name,
                "thumbnail_url": f"/api/thumbnail/{photo.id}/",  # type: ignore[attr-defined]
                "gps_latitude": str(photo.gps_latitude) if photo.gps_latitude else None,
                "gps_longitude": str(photo.gps_longitude) if photo.gps_longitude else None,
                "location": photo.location or "",
                "country_code": photo.country_code or "",
                "date_time_taken": (
                    photo.date_time_taken.isoformat() if photo.date_time_taken else None
                ),
                "is_location_manual": photo.is_location_manual,
            }
        )

    return JsonResponse({"photos": photos, "count": len(photos)})


@require_GET  # type: ignore[misc]
def api_thumbnail(request, photo_id):
    """
    Serve thumbnail for a photo.

    Generates thumbnail on-demand if not cached.
    """
    try:
        photo = Photo.objects.get(id=photo_id)
    except Photo.DoesNotExist:
        raise Http404("Photo not found")

    # Initialize thumbnail service
    thumbnail_service = ThumbnailService(
        cache_dir=settings.THUMBNAIL_CACHE_DIR,
        thumbnail_size=settings.THUMBNAIL_SIZE,
        base_dir=settings.PHOTOS_BASE_DIR,
    )

    try:
        thumbnail_path = thumbnail_service.generate_thumbnail(photo)
        response = FileResponse(open(thumbnail_path, "rb"), content_type="image/jpeg")
        response["Cache-Control"] = "public, max-age=86400"  # Cache for 1 day
        return response
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"Error generating thumbnail for photo {photo_id}: {e}")
        raise Http404(f"Thumbnail generation failed: {str(e)}")


@require_GET  # type: ignore[misc]
def api_photo_preview(request, photo_id):
    """
    Serve a larger preview of the photo for modal display.

    Serves the original photo file directly.
    """
    try:
        photo = Photo.objects.get(id=photo_id)
    except Photo.DoesNotExist:
        raise Http404("Photo not found")

    # Construct full path to photo
    if not photo.source_file:
        raise Http404("Photo source file not found")

    # Security check: ensure no path traversal
    if ".." in photo.source_file or photo.source_file.startswith("/"):
        raise Http404("Invalid file path")

    photo_path = Path(settings.PHOTOS_BASE_DIR) / photo.source_file

    if not photo_path.exists():
        raise Http404(f"Photo file not found: {photo.source_file}")

    try:
        # Determine content type based on file extension
        suffix = photo_path.suffix.lower()
        content_type_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".heic": "image/heic",
            ".webp": "image/webp",
        }
        content_type = content_type_map.get(suffix, "image/jpeg")

        response = FileResponse(open(photo_path, "rb"), content_type=content_type)
        response["Cache-Control"] = "public, max-age=86400"  # Cache for 1 day
        return response
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"Error serving preview for photo {photo_id}: {e}")
        raise Http404(f"Photo preview failed: {str(e)}")


@require_POST  # type: ignore[misc]
def api_update_location(request):
    """
    Update GPS coordinates for selected photos and reverse geocode.

    Request body (JSON):
        photo_ids: List of photo IDs
        latitude: GPS latitude
        longitude: GPS longitude
    """
    try:
        data = json.loads(request.body)
        photo_ids = data.get("photo_ids", [])
        latitude = data.get("latitude")
        longitude = data.get("longitude")

        # Validate inputs
        if not photo_ids:
            return JsonResponse({"success": False, "error": "No photos selected"}, status=400)

        if latitude is None or longitude is None:
            return JsonResponse(
                {"success": False, "error": "GPS coordinates required"}, status=400
            )

        # Validate GPS coordinates
        try:
            lat = float(latitude)
            lng = float(longitude)
            if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
                raise ValueError("Coordinates out of range")
        except (ValueError, TypeError) as e:
            return JsonResponse(
                {"success": False, "error": f"Invalid coordinates: {e}"}, status=400
            )

        # Get photos
        photos = Photo.objects.filter(id__in=photo_ids)
        if not photos.exists():
            return JsonResponse({"success": False, "error": "No valid photos found"}, status=404)

        # Update GPS coordinates and set manual flag
        for photo in photos:
            photo.gps_latitude = Decimal(str(lat))
            photo.gps_longitude = Decimal(str(lng))
            photo.is_location_manual = True

        # Reverse geocode to get location and country code
        geocoding_service = GeocodingService(google_api_key=settings.GOOGLE_MAPS_API_KEY)

        try:
            location_info = geocoding_service._geocode_coordinates(lat, lng)

            # Update all photos with location info
            if location_info:
                for photo in photos:
                    photo.location = location_info.get("location", "")
                    photo.country_code = location_info.get("country_code", "")
                    photo.geo_coded_at = timezone.now()
                    photo.save()

                return JsonResponse(
                    {
                        "success": True,
                        "count": len(photos),
                        "location": location_info.get("location", ""),
                        "country_code": location_info.get("country_code", ""),
                    }
                )

        except Exception as e:
            # Save GPS coordinates even if geocoding fails
            for photo in photos:
                photo.save()

            logger.error(f"Geocoding failed: {e}")
            return JsonResponse(
                {
                    "success": True,
                    "count": len(photos),
                    "warning": f"GPS updated but geocoding failed: {str(e)}",
                }
            )

    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.error(f"Error updating location: {e}")
        return JsonResponse({"success": False, "error": str(e)}, status=500)


@require_GET  # type: ignore[misc]
def api_reverse_geocode(request):
    """
    Reverse geocode coordinates to get location info.

    Query params:
        lat: Latitude
        lng: Longitude
    """
    try:
        lat = float(request.GET.get("lat", ""))
        lng = float(request.GET.get("lng", ""))

        if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
            return JsonResponse({"error": "Invalid coordinates"}, status=400)

        geocoding_service = GeocodingService(google_api_key=settings.GOOGLE_MAPS_API_KEY)
        location_info = geocoding_service._geocode_coordinates(lat, lng)

        return JsonResponse(location_info)

    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid coordinates"}, status=400)
    except Exception as e:
        logger.error(f"Reverse geocoding error: {e}")
        return JsonResponse({"error": str(e)}, status=500)
