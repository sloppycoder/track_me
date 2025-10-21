# Photo Geocoding with Google Maps API

Automatically geocode photos and calculate timezone-aware timestamps using Google Maps Geocoding and Timezone APIs.

## Features

- **Batch Geocoding**: Groups photos by H3 spatial index to minimize API calls
- **Location Information**: Retrieves formatted address and country code
- **Timezone Calculation**: Converts EXIF datetime to timezone-aware timestamps
- **Incremental Processing**: Only geocodes photos that need it
- **Cost Efficient**: One API call per unique location (H3 cell)

## Prerequisites

### Required Python Packages

```bash
# Install required packages
pip install googlemaps pytz

# Or add to requirements.txt
googlemaps>=4.10.0
pytz>=2024.1
```

### Google Maps API Key

1. **Create Google Cloud Project**:
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project or select existing one

2. **Enable APIs**:
   - Enable **Geocoding API**
   - Enable **Time Zone API**

3. **Create API Key**:
   - Go to "APIs & Services" → "Credentials"
   - Click "Create Credentials" → "API Key"
   - Copy the API key

4. **Secure API Key** (Recommended):
   - Click on your API key to edit it
   - Under "Application restrictions", choose "IP addresses" or "HTTP referrers"
   - Under "API restrictions", select "Restrict key" and choose:
     - Geocoding API
     - Time Zone API
   - Save

5. **Set Up Authentication**:

**Option A: Environment Variable** (Recommended)
```bash
export GOOGLE_MAPS_API_KEY="your_api_key_here"
```

**Option B: Django Settings**
```python
# track_me/settings.py
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "your_api_key_here")
```

**Option C: Command Line**
```bash
python manage.py geocode_photos --api-key="your_api_key_here"
```

## Usage

### Command Line

```bash
# Geocode all photos (default H3 resolution 9 = ~11km²)
python manage.py geocode_photos

# Use finer resolution (12 = ~0.3km² = more API calls but more precise)
python manage.py geocode_photos --h3-resolution=12

# Use coarser resolution (6 = ~290km² = fewer API calls but less precise)
python manage.py geocode_photos --h3-resolution=6

# Force recalculate even if already geocoded
python manage.py geocode_photos --recalculate

# Provide API key via command line
python manage.py geocode_photos --api-key="your_key_here"
```

### Background Task (Django-Q2)

```python
from django_q.tasks import async_task

# Queue geocoding task
task_id = async_task(
    'myphoto.tasks.geocode_photos_task',
    h3_resolution=9,
    recalculate=False
)

# Check task status
from django_q.tasks import result
task_result = result(task_id)
```

### Programmatic Usage

```python
from myphoto.services.geocoding_service import GeocodingService

# Create service
service = GeocodingService(
    google_api_key="your_key_here",
    progress_callback=print
)

# Geocode photos
stats = service.geocode_photos(
    h3_resolution=9,  # Grouping resolution
    recalculate=False
)

print(f"Processed: {stats['processed_photos']}, API calls: {stats['api_calls']}")
```

## How It Works

### 1. H3-Based Grouping

Photos are grouped by H3 spatial index to minimize API calls:

```
Resolution 6:  ~290 km²  → Very coarse, few API calls
Resolution 9:  ~11 km²   → Recommended balance (default)
Resolution 12: ~0.3 km²  → Fine-grained, more API calls
Resolution 15: ~0.9 m²   → Very precise, many API calls
```

**Example**: If you have 1000 photos:
- Without grouping: 1000 API calls
- With H3 res 9: ~50-100 API calls (photos grouped by city/neighborhood)
- With H3 res 6: ~10-20 API calls (photos grouped by region)

### 2. Google Geocoding API Call

For each H3 cell, one API call is made:

```python
# Input: H3 cell center coordinates
lat, lon = h3.cell_to_latlng("8928308280fffff")

# API Call
results = gmaps.reverse_geocode((lat, lon))

# Returns:
{
  "formatted_address": "1600 Amphitheatre Parkway, Mountain View, CA 94043, USA",
  "address_components": [
    {"short_name": "US", "types": ["country"]},
    ...
  ]
}
```

### 3. Timezone API Call

```python
# API Call
timezone_result = gmaps.timezone((lat, lon))

# Returns:
{
  "status": "OK",
  "timeZoneId": "America/Los_Angeles",
  "timeZoneName": "Pacific Daylight Time"
}
```

### 4. Data Population

All photos in the same H3 cell share the geocoding result:

```python
for photo in photos_in_h3_cell:
    photo.location = "1600 Amphitheatre Parkway, Mountain View, CA, USA"
    photo.country_code = "US"
    photo.geo_coded_at = now()

    # Calculate timezone-aware datetime
    if photo.date_time_original_text:
        photo.date_time_taken = localize_datetime(
            photo.date_time_original_text,
            "America/Los_Angeles"
        )
```

## Cost Estimation

### Google Maps API Pricing (as of 2024)

- **Geocoding API**: $5 per 1000 requests
- **Time Zone API**: $5 per 1000 requests

**Free tier**: $200 credit per month (40,000 geocoding requests)

### Example Costs

| Photos | H3 Res | API Calls | Cost |
|--------|--------|-----------|------|
| 10,000 | 9      | ~200      | $2   |
| 10,000 | 12     | ~500      | $5   |
| 50,000 | 9      | ~1,000    | $10  |
| 50,000 | 6      | ~100      | $1   |

**Recommendation**: Start with H3 resolution 9 for the best balance.

## Database Schema

### New Field

- **`geo_coded_at`** (DateTimeField): When geocoding was last performed

### Updated Fields

- **`location`** (CharField): Formatted address from Google
- **`country_code`** (CharField): ISO 3166-1 alpha-2 country code
- **`date_time_taken`** (DateTimeField): Timezone-aware datetime

## Workflow Example

```bash
# 1. Process photos (extract EXIF, GPS, H3 indexes, hashes)
python manage.py process_photos /path/to/photos

# 2. Geocode photos (get location info, calculate timezones)
python manage.py geocode_photos

# 3. Query photos by location
python manage.py shell
>>> from myphoto.models import Photo
>>> photos_in_us = Photo.objects.filter(country_code='US')
>>> photos_in_sf = Photo.objects.filter(location__icontains='San Francisco')
```

## Skip Logic

Photos are skipped if:
- Already geocoded (`geo_coded_at` is not NULL) and `--recalculate` not used
- No GPS coordinates

Force reprocess:
```bash
python manage.py geocode_photos --recalculate
```

## Timezone Calculation

If `date_time_original_text` exists (e.g., "2023:10:15 14:30:25"):

1. Parse EXIF datetime format
2. Apply timezone from Google Timezone API
3. Store as timezone-aware `date_time_taken`

Example:
```python
# Input
date_time_original_text = "2023:10:15 14:30:25"
timezone_id = "America/Los_Angeles"

# Output
date_time_taken = datetime(2023, 10, 15, 14, 30, 25, tzinfo=America/Los_Angeles)
```

## Error Handling

Errors are logged but don't stop processing:

```python
stats = service.geocode_photos(h3_resolution=9)

if stats['errors'] > 0:
    print(f"Errors: {stats['errors']}")
    for error in stats['error_details']:
        print(f"  - {error}")
```

Common errors:
- API key invalid or quota exceeded
- No geocoding result for location (ocean, remote areas)
- Rate limiting (too many requests)

## Best Practices

1. **Start Coarse**: Use H3 resolution 6 or 9 initially to minimize costs
2. **Test First**: Test with a small batch (--recalculate on subset)
3. **Monitor Costs**: Check Google Cloud Console regularly
4. **Set Budget Alerts**: Configure billing alerts in Google Cloud
5. **Use Background Tasks**: Run as django-q2 task for large collections
6. **Incremental Updates**: Run periodically to geocode new photos only

## Reprocessing

When reprocessing photos with `--force-reprocess`:

```bash
python manage.py process_photos /path/to/photos --force-reprocess
```

The geocoding data is automatically reset (`geo_coded_at` set to NULL) if GPS coordinates exist. This ensures photos are re-geocoded on the next geocoding run.

## Testing

Run tests with mocked Google Maps API:

```bash
pytest tests/test_geocoding.py -v
```

All tests use mocked API responses (no actual API calls or costs).

## Troubleshooting

### "Google Maps API key required"

Set API key in environment or settings:
```bash
export GOOGLE_MAPS_API_KEY="your_key_here"
```

### "googlemaps library required"

Install package:
```bash
pip install googlemaps pytz
```

### No location data returned

- Check if coordinates are valid
- Remote/ocean locations may have no address
- Check Google Cloud Console for API errors

### API quota exceeded

- Check quota in Google Cloud Console
- Increase quota or wait for reset
- Use coarser H3 resolution

### Timezone calculation fails

- Ensure `pytz` is installed
- Check `date_time_original_text` format (should be "YYYY:MM:DD HH:MM:SS")
- Errors are logged but don't stop geocoding

## Advanced Usage

### Custom H3 Resolution Per Region

```python
service = GeocodingService(google_api_key="your_key")

# Fine resolution for city photos
city_photos = Photo.objects.filter(h3_res_6='86283...')
service.geocode_photos(h3_resolution=12, recalculate=False)

# Coarse resolution for rural photos
rural_photos = Photo.objects.filter(country_code='US').exclude(h3_res_6='86283...')
service.geocode_photos(h3_resolution=6, recalculate=False)
```

### Geocode Specific Photos

```python
from myphoto.models import Photo

# Mark photos for reprocessing
photos_to_update = Photo.objects.filter(country_code='XX')
photos_to_update.update(geo_coded_at=None)

# Run geocoding
service.geocode_photos(h3_resolution=9)
```

## API Reference

### GeocodingService

```python
class GeocodingService:
    def __init__(self, google_api_key: str = None, progress_callback = None)

    def geocode_photos(
        self,
        h3_resolution: int = 9,
        batch_size: int = 100,
        recalculate: bool = False
    ) -> dict
```

**Returns**:
```python
{
    "total_photos": 1000,
    "processed_photos": 950,
    "skipped_photos": 50,
    "api_calls": 127,
    "errors": 0,
    "error_details": []
}
```

## See Also

- [Photo Processing README](./PHOTO_PROCESSING_README.md)
- [H3 Spatial Indexing](https://h3geo.org/)
- [Google Geocoding API](https://developers.google.com/maps/documentation/geocoding)
- [Google Time Zone API](https://developers.google.com/maps/documentation/timezone)
