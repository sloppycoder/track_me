# Photo Management System - Quick Reference

## Available Commands (Clean & Simple)

```bash
# 1. Process photos (EXIF + GPS + H3 + Hashes)
python manage.py process_photos /path/to/photos

# 2. Estimate geocoding costs before running
python manage.py estimate_geocoding_cost

# 3. Geocode photos (Location + Timezone)
python manage.py geocode_photos --h3-resolution=12

# 4. Validate against CSV (optional data integrity check)
python manage.py validate_photos /path/to/export.csv
```

## Your Workflow

```bash
# Step 1: Process all photos
python manage.py process_photos /Volumes/PhotoArchive

# Step 2: Check geocoding costs
python manage.py estimate_geocoding_cost
# → Shows you exactly how many API calls needed for each resolution

# Step 3: Geocode (use resolution 12 if within free tier)
python manage.py geocode_photos --h3-resolution=12

# Done! Your photos now have:
# - EXIF metadata (JSON)
# - GPS coordinates
# - H3 spatial indexes (5 resolutions)
# - Perceptual hashes (duplicate detection)
# - Location & country code
# - Timezone-aware timestamps
```

## What Each Command Does

### `process_photos`
- Discovers all image files (.jpg, .png, .heic, etc.)
- Extracts EXIF metadata → stores as JSON
- Extracts GPS coordinates
- Calculates H3 indexes at 5 resolutions
- Generates perceptual hash for duplicate detection
- **Automatic**: H3 indexing and hashing built-in!

### `estimate_geocoding_cost`
- Shows how many unique locations at each H3 resolution
- Estimates API calls needed
- Calculates cost
- Tells you if you're within Google's free tier

### `geocode_photos`
- Groups photos by H3 cell (minimizes API calls!)
- Calls Google Geocoding API once per location
- Gets formatted address & country code
- Gets timezone from Google Timezone API
- Calculates timezone-aware timestamps

### `validate_photos`
- Compares database against CSV export
- Checks for missing files
- Validates GPS coordinates
- Validates timestamps
- Reports mismatches

## Services (2 Core Services)

```
myphoto/services/
├── photo_processing_service.py   # Handles EXIF, GPS, H3, hashing
└── geocoding_service.py          # Handles Google Maps API
```

All processing logic is automatic - no separate commands needed for H3 or hashing!

## Documentation

- [PHOTO_PROCESSING_README.md](myphoto/PHOTO_PROCESSING_README.md) - Photo processing details
- [GEOCODING_README.md](myphoto/GEOCODING_README.md) - Google Maps setup & usage
- [VALIDATION_README.md](myphoto/VALIDATION_README.md) - CSV validation guide
- [DUPLICATE_DETECTION_README.md](myphoto/DUPLICATE_DETECTION_README.md) - Perceptual hashing info

## Tests

```bash
pytest -v
# 14 tests passing ✓
# - 7 photo processing tests
# - 7 geocoding tests (with mocked Google API)
```

## Key Features

✅ **All-in-one processing**: EXIF + GPS + H3 + Hash in one command
✅ **Cost optimization**: H3-based batching reduces API calls 50-100x
✅ **Incremental**: Safe to re-run, only processes new photos
✅ **Free tier friendly**: Estimate costs before geocoding
✅ **Street-level precision**: Resolution 12 gives ~0.3km² accuracy

## Cost Example

**10,000 photos with resolution 12:**
- Without H3 grouping: 20,000 API calls (~$100)
- With H3 grouping: ~1,000 API calls (~$5) ✓

Most photo collections fit within Google's $200/month free tier!
