# Duplicate Photo Detection with Perceptual Hashing

Find duplicate photos even when they're different resolutions (thumbnail vs full-size)!

## How It Works

Uses **perceptual hashing (pHash)** which creates a "fingerprint" based on the visual content of an image, not the file bytes. This means:

✅ **Finds duplicates across different resolutions**
- Original 4000x3000 photo
- Thumbnail 800x600 version
- Instagram cropped 1080x1080 version

✅ **Detects similar images with minor edits**
- Slight color adjustments
- Small crops
- Minor filters

## Three Hash Types

### 1. Perceptual Hash (pHash) - **RECOMMENDED**
- Most accurate for finding duplicates
- Works across different resolutions
- Resistant to minor edits
- **Use this for duplicate detection**

### 2. Average Hash (aHash)
- Faster but less accurate
- Good for rough similarity

### 3. Difference Hash (dHash)
- Gradient-based
- Good for detecting minor edits

## Usage

### Calculate Hashes for Photos

You need to provide the base directory where your actual photo files are stored:

```bash
# Calculate perceptual hashes (recommended)
python manage.py calculate_photo_hashes /path/to/photos

# Calculate all hash types
python manage.py calculate_photo_hashes /path/to/photos --hash-types perceptual,average,difference

# Recalculate hashes (if you want to update)
python manage.py calculate_photo_hashes /path/to/photos --recalculate

# Find duplicates immediately after calculation
python manage.py calculate_photo_hashes /path/to/photos --find-duplicates

# Adjust duplicate detection sensitivity (0-10)
python manage.py calculate_photo_hashes /path/to/photos --find-duplicates --threshold 5
```

**Threshold Guidelines:**
- `0-2`: Exact or near-exact duplicates
- `3-5`: Very similar (different resolutions) - **RECOMMENDED**
- `6-10`: Similar images (may catch edited versions)
- `11+`: Too loose, many false positives

### Find Duplicates Programmatically

```python
from myphoto.services.photo_hashing_service import PhotoHashingService

service = PhotoHashingService()

# Find duplicates (threshold 0-5 recommended)
duplicates = service.find_duplicates(threshold=5)

for group in duplicates:
    print(f"Found {len(group['photos'])} duplicates:")
    for photo in group['photos']:
        print(f"  - {photo.file_name} ({photo.source_file})")
```

### Compare Two Specific Images

```python
from myphoto.services.photo_hashing_service import PhotoHashingService

service = PhotoHashingService()

# Compare two image files
result = service.compare_images(
    '/path/to/photo1.jpg',
    '/path/to/photo2.jpg'
)

print(f"Perceptual distance: {result['perceptual_distance']}")
print(f"Are duplicates? {result['are_duplicates']}")  # True if distance <= 5
print(f"Are similar? {result['are_similar']}")        # True if distance <= 10
```

### Calculate Hash for a Single File

```python
from myphoto.services.photo_hashing_service import PhotoHashingService

service = PhotoHashingService()

hashes = service.calculate_hash_for_file('/path/to/photo.jpg')
print(hashes)
# {'perceptual': 'a3f8d9c2...', 'average': 'b4e7c1a3...', 'difference': 'c5d2e8f1...'}
```

## Understanding Hamming Distance

The "distance" between two hashes is the **Hamming distance** - how many bits differ:

- Distance `0`: Exact match
- Distance `1-5`: Very similar (likely duplicates at different resolutions)
- Distance `6-10`: Similar (same photo with edits, crops, filters)
- Distance `11-15`: Somewhat similar
- Distance `16+`: Different photos

## Example Workflow

### 1. Import Photos
```bash
python manage.py import_photos /path/to/export.csv
```

### 2. Calculate Hashes
```bash
# Point to where your actual photo files are stored
python manage.py calculate_photo_hashes /Volumes/GooglePhotos --find-duplicates
```

**Output:**
```
Processing 1000 photos...
Processed 1000/1000 photos...

============================================================
Successfully processed 1000 photos
Skipped 50 photos

============================================================
Finding duplicates...
Found 15 groups of potential duplicates:

Group 1:
  - IMG_1234.jpg (ID: 45) - ./2020/01/IMG_1234.jpg
  - IMG_1234_thumb.jpg (ID: 892) - ./thumbnails/IMG_1234_thumb.jpg

Group 2:
  - vacation.jpg (ID: 156) - ./2021/06/vacation.jpg
  - vacation_instagram.jpg (ID: 1043) - ./instagram/vacation_instagram.jpg
```

### 3. Query Duplicates in Code

```python
from myphoto.models import Photo
from myphoto.services.photo_hashing_service import PhotoHashingService

# Get all photos with hashes
photos_with_hashes = Photo.objects.filter(perceptual_hash__isnull=False).count()
print(f"{photos_with_hashes} photos have perceptual hashes")

# Find duplicates
service = PhotoHashingService()
duplicates = service.find_duplicates(threshold=5)

print(f"Found {len(duplicates)} duplicate groups")

# Handle duplicates (example: keep newest, delete others)
for dup_group in duplicates:
    photos = dup_group['photos']
    # Sort by created_at, keep the newest
    newest = max(photos, key=lambda p: p.created_at)
    to_delete = [p for p in photos if p.id != newest.id]

    print(f"Keeping: {newest.file_name}")
    for photo in to_delete:
        print(f"  Deleting duplicate: {photo.file_name}")
        # photo.delete()  # Uncomment to actually delete
```

## Database Schema

The following fields are added to the `Photo` model:

```python
perceptual_hash     # pHash (16 chars) - RECOMMENDED for duplicates
average_hash        # aHash (16 chars) - faster, less accurate
difference_hash     # dHash (16 chars) - gradient-based
```

All three fields are indexed for fast lookups.

## Background Task Integration

```python
# In myphoto/tasks.py (add this function)

def calculate_photo_hashes_task(base_path: str, recalculate: bool = False):
    """Background task to calculate perceptual hashes."""
    from myphoto.services.photo_hashing_service import PhotoHashingService
    import logging

    logger = logging.getLogger(__name__)

    def log_progress(msg):
        logger.info(msg)

    service = PhotoHashingService(progress_callback=log_progress)

    stats = service.calculate_hashes_for_photos(
        base_path=base_path,
        batch_size=100,
        recalculate=recalculate,
        hash_types=['perceptual']
    )

    logger.info(f"Hashing completed: {stats['updated']} processed")
    return stats

# Queue the task
from django_q.tasks import async_task
async_task('myphoto.tasks.calculate_photo_hashes_task', '/path/to/photos')
```

## Performance Notes

- **Hashing Speed**: ~100-200 photos/second (depends on file size and CPU)
- **Duplicate Detection**: O(n²) - can be slow with 10k+ photos
  - For large collections, consider chunking by date ranges
  - Or use database queries to narrow down candidates first

## Tips

1. **Start with perceptual hash only** - it's the most accurate
2. **Use threshold 3-5** for finding duplicates across resolutions
3. **Store original file paths** - you'll need them to access the actual images
4. **Batch process** - use `--batch-size 100` for better memory usage
5. **Test threshold** - try different values to find the sweet spot for your collection

## Technical Details

- Uses `imagehash` library (built on PIL/Pillow)
- Hash is 64-bit (stored as 16-character hex string)
- Comparison uses Hamming distance (count of differing bits)
- Supports JPEG, PNG, GIF, BMP, TIFF formats

## Limitations

- ❌ **Requires access to actual image files** (not just metadata)
- ❌ **Cannot detect duplicates from EXIF metadata alone**
- ❌ **Heavily cropped or rotated images may not match**
- ❌ **Different photos of same subject won't match**

The perceptual hash is based on visual content, so you must have access to the image files to calculate it!
