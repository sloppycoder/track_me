# Scripts

## create_test_photos.py

Creates test photos for development/testing by randomly selecting photos from a directory, scaling them down to 256px width, and preserving all EXIF metadata including GPS coordinates.

### Usage

```bash
# Basic usage - select 10 random photos, scale to 256px
python scripts/create_test_photos.py /path/to/photos /path/to/output

# Select 20 photos
python scripts/create_test_photos.py /path/to/photos /path/to/output --count 20

# Custom width (512px)
python scripts/create_test_photos.py /path/to/photos /path/to/output --width 512

# Use random seed for reproducible selection
python scripts/create_test_photos.py /path/to/photos /path/to/output --seed 42
```

### Features

- ✅ Randomly selects photos from directory (recursive)
- ✅ Tries to select diverse file types (.HEIC, .JPG, .PNG, .jpeg, .jpg, .png)
- ✅ Scales to specified width (default 256px) while preserving aspect ratio
- ✅ **Preserves all EXIF metadata including GPS coordinates**
- ✅ High quality scaling (Lanczos resampling)
- ✅ Verifies EXIF preservation
- ✅ Shows progress and summary

### Example Output

```bash
$ python scripts/create_test_photos.py /Volumes/Photos test_photos --count 10

Discovering photos in: /Volumes/Photos
Found 5432 photos

Photo distribution by type:
  .jpg: 3421 photos
  .png: 1532 photos
  .heic: 479 photos

Selecting 10 photos (3 per type + 1 extra)

Selected 10 photos

Scaling photos to 256px width...
============================================================
[1/10] IMG_1234.jpg
  ✓ EXIF preserved (including GPS)
[2/10] vacation.png
  ✓ EXIF preserved
[3/10] sunset.HEIC
  ✓ EXIF preserved (including GPS)
...
============================================================

Summary:
  Successfully processed: 10/10
  Output directory: test_photos
  Image width: 256px

✓ All photos processed successfully!
```

### Options

| Option | Description | Default |
|--------|-------------|---------|
| `source_dir` | Source directory containing photos | Required |
| `output_dir` | Output directory for test photos | Required |
| `--count` | Number of photos to select | 10 |
| `--width` | Target width in pixels | 256 |
| `--seed` | Random seed for reproducible selection | None |

### Use Cases

**Testing photo processing:**
```bash
python scripts/create_test_photos.py ~/Photos test_photos --count 10
python manage.py process_photos test_photos
```

**Creating sample dataset:**
```bash
python scripts/create_test_photos.py /Volumes/Archive samples --count 100 --width 512
```

**Reproducible test set:**
```bash
python scripts/create_test_photos.py ~/Photos test_photos --seed 42
# Will always select the same photos
```

### Technical Details

- Uses PIL/Pillow for image processing
- Uses pillow-heif for HEIC format support (required)
- Lanczos resampling for high-quality downscaling
- EXIF data is preserved via PIL's `exif` parameter
- Aspect ratio maintained automatically
- GPS coordinates preserved in EXIF
- Supports HEIC, JPG/JPEG, PNG formats
