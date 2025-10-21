# Photo Processing Service

Process photo files directly to extract EXIF metadata, GPS coordinates, calculate H3 spatial indexes, and generate perceptual hashes for duplicate detection.

## Overview

The `PhotoProcessingService` replaces the CSV-based import workflow with direct photo file processing. It automatically:

1. **Discovers photo files** recursively in a directory
2. **Extracts EXIF metadata** and stores in JSON field
3. **Extracts GPS coordinates** from EXIF
4. **Calculates H3 spatial indexes** at multiple resolutions (if GPS available)
5. **Generates perceptual hashes** for duplicate detection
6. **Updates or creates** database records based on relative file paths

## Supported File Types

- `.jpg` / `.jpeg`
- `.png`
- `.heic`
- `.webp`
- `.psd`

Video files (`.avi`, `.mov`, `.mp4`, `.mpg`) are currently not supported.

## Usage

### Command Line

```bash
# Process all photos in a directory
python manage.py process_photos /path/to/photos

# Force reprocess all photos (even if already processed)
python manage.py process_photos /path/to/photos --force-reprocess
```

### Background Task (Django-Q2)

```python
from django_q.tasks import async_task

# Queue photo processing task
task_id = async_task(
    'myphoto.tasks.process_photos_task',
    '/path/to/photos',
    force_reprocess=False
)
```

### Programmatic Usage

```python
from myphoto.services.photo_processing_service import PhotoProcessingService

service = PhotoProcessingService(progress_callback=print)

# Process directory
stats = service.process_directory(
    directory_path='/path/to/photos',
    force_reprocess=False
)

print(f"Created: {stats['created']}, Updated: {stats['updated']}, Skipped: {stats['skipped']}")
```

## How It Works

### 1. File Discovery

Recursively scans the directory for supported image files:

```python
service._discover_photo_files(directory_path)
```

### 2. Record Matching

- Calculates **relative path** from base directory
- Checks if record with same `source_file` already exists
- Creates new record if not found, updates existing if found

### 3. Skip Logic

Photos are skipped if already fully processed (unless `--force-reprocess` is used):

- **With GPS**: Must have GPS coordinates, H3 indexes, and perceptual hash
- **Without GPS**: Must have perceptual hash

### 4. Processing Steps

For each photo file:

```python
# a. Extract basic info (filename, directory)
_extract_basic_info(photo, file_path, relative_path)

# b. Extract all EXIF metadata → exif_meta JSON field
_extract_exif_metadata(photo, file_path)

# c. Extract GPS coordinates from EXIF
_extract_gps_coordinates(photo)

# d. Calculate H3 indexes (if GPS available)
_calculate_h3_indexes(photo)

# e. Calculate perceptual hash
_calculate_perceptual_hash(photo, file_path)
```

## Database Schema Changes

### New Fields

- **`exif_meta`** (JSONField): Complete EXIF metadata from photo
- **`imported_at`** (DateTimeField): Last time photo was processed (replaces `created_at`)

### Example `exif_meta` Structure

```json
{
  "DateTime": "2023:10:15 14:30:25",
  "Make": "Apple",
  "Model": "iPhone 14 Pro",
  "Orientation": 1,
  "XResolution": 72.0,
  "YResolution": 72.0,
  "Software": "iOS 17.0",
  "GPSInfo": {...}
}
```

## Incremental Processing

The service is designed for incremental imports:

- **First run**: Processes all photos, creates records
- **Subsequent runs**: Only processes new/modified photos
- **Force reprocess**: Use `--force-reprocess` to update all records

Example workflow:

```bash
# Initial import
python manage.py process_photos /Volumes/PhotoArchive

# Add more photos to archive...
# Re-run to process only new photos
python manage.py process_photos /Volumes/PhotoArchive

# Force reprocess everything (e.g., after algorithm improvement)
python manage.py process_photos /Volumes/PhotoArchive --force-reprocess
```

## Statistics Output

```
Found 1523 photo files in /path/to/photos
Progress: 100/1523 files
Progress: 200/1523 files
...

============================================================
Total files found: 1523
Created 145 new photo records
Updated 23 existing photo records
Skipped 1355 already processed photos
============================================================
```

## Performance

- **File discovery**: Fast (uses `os.walk`)
- **EXIF extraction**: ~50-100 files/second
- **H3 indexing**: Very fast (in-memory calculation)
- **Perceptual hashing**: ~100-200 files/second (depends on image size)
- **Progress updates**: Every 10 files

For large collections (10,000+ photos), consider:
- Running as background task with Django-Q2
- Processing in batches by subdirectory
- Using SSD storage for better I/O performance

## Error Handling

Errors are logged and counted but don't stop processing:

```python
stats = service.process_directory('/path')

if stats['errors'] > 0:
    print(f"Errors: {stats['errors']}")
    for error in stats['error_details']:
        print(f"  - {error}")
```

Common errors:
- Corrupted image files
- Unsupported EXIF formats
- Permission denied
- File moved/deleted during processing

## GPS Coordinate Extraction

The service attempts to extract GPS coordinates from EXIF metadata. GPS data in EXIF can be in various formats:

- **Decimal degrees**: `37.7749, -122.4194`
- **DMS (Degrees, Minutes, Seconds)**: `(37, 46, 29.64)`

The service handles both formats automatically.

**Note**: GPS extraction from EXIF is complex. Some photos may have GPS data in non-standard formats that aren't detected. Consider using a specialized library like `piexif` or `exif` for more robust GPS parsing if needed.

## Legacy CSV Import

The old CSV import workflow is still available:

```bash
# Import from exiftool CSV export (legacy method)
python manage.py import_photos /path/to/export.csv
```

Service file: `myphoto/services/photo_import_from_csv_service.py`

## Migration Notes

If you have existing data imported via CSV:

1. The new service uses `source_file` as the unique identifier
2. Records will be **updated** (not duplicated) if `source_file` matches
3. Run `process_photos` to backfill EXIF metadata and perceptual hashes:

```bash
# Backfill existing records with EXIF and hashes
python manage.py process_photos /path/to/photos --force-reprocess
```

## Testing

Run tests:

```bash
pytest tests/test_photo_processing.py -v
```

Tests cover:
- File discovery
- Single photo processing
- Directory processing
- Skip logic
- Force reprocess
- EXIF extraction
- Perceptual hash calculation

All 28 tests passing ✓

## Troubleshooting

### No GPS coordinates extracted

- Check if photos have GPS data: `exiftool -GPS* photo.jpg`
- GPS data may be in non-standard format
- Consider using specialized EXIF library

### Perceptual hash not calculated

- Ensure PIL/Pillow can open the image
- Check file is not corrupted
- Verify file extension is supported

### Photos skipped unexpectedly

- Check if `perceptual_hash` field is populated
- Use `--force-reprocess` to override skip logic
- Verify file path matches `source_file` in database

### Large memory usage

- Processing thousands of images keeps them in memory briefly
- Reduce batch size if memory constrained
- Process subdirectories separately
