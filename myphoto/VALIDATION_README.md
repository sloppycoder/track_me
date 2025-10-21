# Photo Validation

Validate imported photo records against the original CSV file to ensure data integrity.

## Purpose

After processing photos from files, use this command to validate that all data was correctly extracted by comparing against the CSV file exported by exiftool.

## Usage

```bash
# Validate photos against CSV file
python manage.py validate_photos /path/to/export.csv
```

## What It Checks

For each row in the CSV file:

1. **File Existence**: Checks if the photo exists in the database
   - Warning if file is missing from database

2. **GPS Coordinates**: Compares GPS data between CSV and database
   - Warning if CSV has GPS but database doesn't
   - Warning if GPS values differ by more than 0.0001 degrees

3. **Timestamp**: Compares DateTimeOriginal field
   - Warning if timestamp text doesn't match exactly

## Example Output

```bash
$ python manage.py validate_photos /tmp/photos-metadata.csv

Validating photos from: /tmp/photos-metadata.csv
============================================================
Row 145: MISSING - IMG_1234.jpg (source: ./2020/01/IMG_1234.jpg)
Row 892: GPS MISMATCH - vacation.jpg (CSV has GPS, DB missing)
Row 1043: TIMESTAMP MISMATCH - sunset.jpg (CSV: '2023:10:15 14:30:25' vs DB: '2023:10:15 14:30:24')
Processed 5000 rows...

============================================================
Total rows processed: 5000
Matched: 4997
Missing from DB: 1
GPS mismatches: 1
Timestamp mismatches: 1
============================================================

⚠ Found 3 issues (see warnings above)
```

## Success Output

```bash
$ python manage.py validate_photos /tmp/photos-metadata.csv

Validating photos from: /tmp/photos-metadata.csv
============================================================
Processed 5000 rows...

============================================================
Total rows processed: 5000
Matched: 5000
============================================================

✓ All photos validated successfully! No issues found.
```

## Common Issues

### Missing Files
Photos in CSV but not in database:
- Photo file might not have been processed
- File path might be different
- File might have been skipped due to errors

**Solution**: Re-run photo processing for missing files

### GPS Mismatches
GPS coordinates don't match between CSV and database:
- EXIF extraction might have failed
- Different GPS format in EXIF vs CSV
- Corruption in image file

**Solution**: Check EXIF data manually with `exiftool`

### Timestamp Mismatches
DateTimeOriginal doesn't match:
- EXIF format variations
- Timezone differences
- EXIF extraction issues

**Solution**: Verify EXIF data with `exiftool -DateTimeOriginal photo.jpg`

## Workflow

```bash
# 1. Export metadata from photos using exiftool
exiftool -csv -r /path/to/photos > photos-metadata.csv

# 2. Process photos directly
python manage.py process_photos /path/to/photos

# 3. Validate against CSV
python manage.py validate_photos photos-metadata.csv

# 4. Fix any issues and re-process
python manage.py process_photos /path/to/specific/photos --force-reprocess
```

## Technical Details

### GPS Tolerance
GPS coordinates are considered matching if they differ by less than 0.0001 degrees:
- ~11 meters at the equator
- Accounts for rounding differences

### Field Mapping
- **SourceFile** (CSV) → `source_file` (DB)
- **GPSLatitude** (CSV) → `gps_latitude` (DB)
- **GPSLongitude** (CSV) → `gps_longitude` (DB)
- **DateTimeOriginal** (CSV) → `date_time_original_text` (DB)

### Performance
- Processes ~1000 rows/second
- Progress updates every 100 rows
- No database writes (read-only validation)

## Related Commands

- `process_photos` - Process photo files and extract metadata
- `estimate_geocoding_cost` - Estimate API calls needed for geocoding
- `geocode_photos` - Geocode photos using Google Maps API
