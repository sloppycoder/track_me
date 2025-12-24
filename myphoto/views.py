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


def parse_smart_search(query_str):
    """
    Smart search parser that automatically detects search intent.

    Parsing logic:
    1. Date range: "2004 to 2006", "jan 2004 to dec 2005", "2004-01-01 to 2006-12-31"
    2. Single date/year/month: "2004", "jan 2004", "2005-01-01"
    3. Country code: 2 uppercase letters (e.g., "US", "SG")
    4. Location text: everything else

    Returns:
        dict with keys:
        - search_type: "date_range", "date", "country_code", "location", or "unknown"
        - date_from: datetime or None
        - date_to: datetime or None
        - text_search: string or None
        - country_code: string or None
    """
    if not query_str or not query_str.strip():
        return {
            "search_type": "unknown",
            "date_from": None,
            "date_to": None,
            "text_search": None,
            "country_code": None,
        }

    query = query_str.strip()

    # Check for date range patterns: "X to Y" or "X - Y"
    # Common patterns: "2004 to 2006", "jan 2004 to dec 2005"
    date_range_patterns = [" to ", " - ", " – ", " — "]  # Various dash types
    for pattern in date_range_patterns:
        if pattern in query.lower():
            parts = query.lower().split(pattern)
            if len(parts) == 2:
                start_str, end_str = parts
                start_date, _ = parse_date_filter(start_str.strip())
                _, end_date = parse_date_filter(end_str.strip())

                if start_date and end_date:
                    return {
                        "search_type": "date_range",
                        "date_from": start_date,
                        "date_to": end_date,
                        "text_search": None,
                        "country_code": None,
                    }
            break  # Only check first matching pattern

    # Check if it's a country code (2 uppercase letters)
    if len(query) == 2 and query.isupper() and query.isalpha():
        return {
            "search_type": "country_code",
            "date_from": None,
            "date_to": None,
            "text_search": None,
            "country_code": query,
        }

    # Try to parse as a date (year, month-year, or specific date)
    # Check if it looks like a date pattern
    date_indicators = [
        query.isdigit() and len(query) == 4,  # Year like "2004"
        any(
            month in query.lower()
            for month in [
                "jan",
                "feb",
                "mar",
                "apr",
                "may",
                "jun",
                "jul",
                "aug",
                "sep",
                "oct",
                "nov",
                "dec",
            ]
        ),  # Month names
        "-" in query and any(c.isdigit() for c in query),  # ISO date format
        "/" in query and any(c.isdigit() for c in query),  # Slash date format
    ]

    if any(date_indicators):
        start_date, end_date = parse_date_filter(query)
        if start_date and end_date:
            return {
                "search_type": "date",
                "date_from": start_date,
                "date_to": end_date,
                "text_search": None,
                "country_code": None,
            }

    # Default to location text search
    return {
        "search_type": "location",
        "date_from": None,
        "date_to": None,
        "text_search": query,
        "country_code": None,
    }


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
    API endpoint to search photos with smart query parsing.

    Query params:
        q: Smart search query that auto-detects:
           - Date ranges: "2004 to 2006", "jan 2004 to dec 2005"
           - Single dates: "2004", "jan 2004", "2005-01-01"
           - Country codes: "US", "SG" (2 uppercase letters)
           - Location text: "Singapore", "Tokyo"
        limit: Maximum results (default: MAX_PHOTOS_PER_PAGE)
    """
    query_str = request.GET.get("q", "").strip()
    limit = int(request.GET.get("limit", settings.MAX_PHOTOS_PER_PAGE))

    # Start with all photos
    queryset = Photo.objects.all()

    # Parse the smart search query
    search_params = parse_smart_search(query_str)

    # Apply filters based on search type
    if search_params["search_type"] == "country_code":
        # Search by country code
        queryset = queryset.filter(country_code__iexact=search_params["country_code"])

    elif search_params["search_type"] == "location":
        # Text search on location or country_code
        queryset = queryset.filter(
            Q(location__icontains=search_params["text_search"])
            | Q(country_code__icontains=search_params["text_search"])
        )

    elif search_params["search_type"] in ["date", "date_range"]:
        # Date filtering
        start_date = search_params["date_from"]
        end_date = search_params["date_to"]

        if start_date and end_date:
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

        # Update GPS coordinates, recalculate H3 indexes, and set manual flag
        for photo in photos:
            photo.gps_latitude = Decimal(str(lat))
            photo.gps_longitude = Decimal(str(lng))
            photo.calculate_h3_indexes()
            photo.is_location_manual = True

        # Reverse geocode to get location and country code
        geocoding_service = GeocodingService(google_api_key=settings.GOOGLE_MAPS_API_KEY)

        try:
            location_info = geocoding_service._geocode_coordinates(lat, lng)

            # Update all photos with location info
            if location_info:
                for photo in photos:
                    photo.location = location_info.get("formatted_address", "")
                    photo.country_code = location_info.get("country_code", "")
                    photo.geo_coded_at = timezone.now()
                    photo.save()

                return JsonResponse(
                    {
                        "success": True,
                        "count": len(photos),
                        "location": location_info.get("formatted_address", ""),
                        "country_code": location_info.get("country_code", ""),
                    }
                )
            else:
                # Geocoding returned empty/None - save GPS only
                for photo in photos:
                    photo.save()

                return JsonResponse(
                    {
                        "success": True,
                        "count": len(photos),
                        "warning": "GPS updated but geocoding returned no location data",
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


def footprints_view(request):
    """Footprints timeline visualization page."""
    return render(
        request,
        "myphoto/footprints.html",
        {
            "api_key": settings.GOOGLE_MAPS_API_KEY,
            "max_steps": settings.MAX_FOOTPRINT_STEPS,
            "min_steps": settings.MIN_FOOTPRINT_STEPS,
        },
    )


@require_GET  # type: ignore[misc]
def api_footprints_steps(request):
    """
    API endpoint to get footprints timeline steps with smart H3 clustering.

    Query params:
        date_range: Date range string (e.g., "2024", "2024-01", "2024-01 to 2024-12")

    Returns JSON with timeline steps, each representing a geographic cluster.
    """
    import h3
    from django.db.models import Min

    date_range_str = request.GET.get("date_range", "").strip()

    if not date_range_str:
        return JsonResponse({"error": "date_range parameter required"}, status=400)

    # Parse date range using existing smart search parser
    search_params = parse_smart_search(date_range_str)

    # Extract date range
    if search_params["search_type"] in ["date", "date_range"]:
        date_from = search_params["date_from"]
        date_to = search_params["date_to"]
    else:
        return JsonResponse(
            {
                "error": (
                    "Invalid date range format. Examples: '2024', '2024-01', '2024-01 to 2024-12'"
                )
            },
            status=400,
        )

    if not date_from or not date_to:
        return JsonResponse({"error": "Could not parse date range"}, status=400)

    # Query photos with GPS in date range
    photos = Photo.objects.filter(
        date_time_taken__gte=date_from,
        date_time_taken__lte=date_to,
        gps_latitude__isnull=False,
        gps_longitude__isnull=False,
    )

    if not photos.exists():
        return JsonResponse(
            {
                "steps": [],
                "total_steps": 0,
                "message": "No photos with GPS found in this date range",
            }
        )

    # Determine initial H3 resolution based on timeline span
    span_days = (date_to - date_from).days

    if span_days >= 365:  # 1+ years
        initial_res = 6
    elif span_days >= 30:  # 1-12 months
        initial_res = 9
    else:  # <1 month
        initial_res = 10

    # Available resolutions in order
    resolutions = [3, 6, 9, 10, 11]
    current_res_idx = resolutions.index(initial_res)

    # Find optimal resolution with cluster count in desired range
    resolution = initial_res
    cluster_count = 0

    for _ in range(len(resolutions)):
        resolution = resolutions[current_res_idx]
        h3_field = f"h3_res_{resolution}"

        # Count unique H3 clusters at this resolution
        cluster_count = photos.values(h3_field).distinct().count()

        # Check if we need to adjust
        if (
            cluster_count < settings.MIN_FOOTPRINT_STEPS
            and current_res_idx < len(resolutions) - 1
        ):
            # Too few clusters, increase resolution (more granular)
            current_res_idx += 1
            continue
        elif cluster_count > settings.MAX_FOOTPRINT_STEPS and current_res_idx > 0:
            # Too many clusters, decrease resolution (less granular)
            current_res_idx -= 1
            continue
        else:
            # Just right or can't adjust further
            break

    # Get earliest photo per H3 cluster
    h3_field = f"h3_res_{resolution}"
    clusters = (
        photos.values(h3_field)
        .annotate(earliest_date=Min("date_time_taken"))
        .order_by("earliest_date")
    )

    # Build steps array
    steps = []
    for idx, cluster in enumerate(clusters):
        h3_cell = cluster[h3_field]
        if not h3_cell:
            continue

        # Get the actual earliest photo in this cluster
        photo = (
            photos.filter(**{h3_field: h3_cell}, date_time_taken=cluster["earliest_date"])
            .order_by("date_time_taken")
            .first()
        )

        if not photo:
            continue

        # Get H3 cluster center coordinates
        try:
            center_lat, center_lng = h3.cell_to_latlng(h3_cell)
        except Exception as e:
            logger.error(f"Error getting H3 center for {h3_cell}: {e}")
            continue

        # Count photos in this cluster
        photo_count = photos.filter(**{h3_field: h3_cell}).count()

        steps.append(
            {
                "step_number": idx + 1,
                "h3_cell": h3_cell,
                "resolution": resolution,
                "photo_id": photo.id,  # type: ignore[attr-defined]
                "file_name": photo.file_name,
                "thumbnail_url": f"/api/thumbnail/{photo.id}/",  # type: ignore[attr-defined]
                "date_time_taken": (
                    photo.date_time_taken.isoformat() if photo.date_time_taken else None
                ),
                "location": photo.location or "",
                "country_code": photo.country_code or "",
                "center_lat": center_lat,
                "center_lng": center_lng,
                "photo_count": photo_count,
            }
        )

    return JsonResponse(
        {
            "steps": steps,
            "total_steps": len(steps),
            "resolution_used": resolution,
            "total_photos": photos.count(),
        }
    )
