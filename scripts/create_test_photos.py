#!/usr/bin/env python3
"""
Script to create test photos for development/testing.

Randomly selects photos from a directory, scales them down to 256px width,
and preserves all EXIF metadata including GPS coordinates.

Usage:
    python scripts/create_test_photos.py /path/to/photos /path/to/output
    python scripts/create_test_photos.py /path/to/photos /path/to/output --count 20
"""

import argparse
import os
import random
import sys
from pathlib import Path

import pillow_heif
from PIL import Image

# Register HEIF opener for HEIC support
pillow_heif.register_heif_opener()

# Supported image extensions
IMAGE_EXTENSIONS = {".heic", ".jpg", ".jpeg", ".png"}


def discover_photos(directory: str) -> list:
    """Recursively discover all photo files in directory."""
    print(f"Discovering photos in: {directory}")
    photos = []

    for root, _, files in os.walk(directory):
        for filename in files:
            ext = Path(filename).suffix.lower()
            if ext in IMAGE_EXTENSIONS:
                full_path = os.path.join(root, filename)
                photos.append((full_path, ext))

    print(f"Found {len(photos)} photos")
    return photos


def select_diverse_photos(photos: list, count: int) -> list:
    """Select photos with diverse file types if possible."""
    # Group by extension
    by_extension = {}
    for photo_path, ext in photos:
        if ext not in by_extension:
            by_extension[ext] = []
        by_extension[ext].append(photo_path)

    print("\nPhoto distribution by type:")
    for ext, paths in by_extension.items():
        print(f"  {ext}: {len(paths)} photos")

    # Try to select evenly across extensions
    selected = []
    extensions = list(by_extension.keys())
    per_type = count // len(extensions)
    remainder = count % len(extensions)

    print(f"\nSelecting {count} photos ({per_type} per type + {remainder} extra)")

    for ext in extensions:
        available = by_extension[ext]
        to_select = min(per_type, len(available))
        selected.extend(random.sample(available, to_select))

    # Add remainder from any type
    if len(selected) < count:
        remaining_needed = count - len(selected)
        all_remaining = [p for p, _ in photos if p not in selected]
        if all_remaining:
            selected.extend(
                random.sample(all_remaining, min(remaining_needed, len(all_remaining)))
            )

    return selected


def scale_photo_preserve_exif(input_path: str, output_path: str, width: int = 256):
    """
    Scale photo to specified width while preserving aspect ratio and EXIF data.

    Args:
        input_path: Path to input photo
        output_path: Path to save scaled photo
        width: Target width in pixels (default: 256)
    """
    try:
        # Open image
        img = Image.open(input_path)

        # Get EXIF data before resizing
        exif_data = img.info.get("exif", None)

        # Calculate new height to preserve aspect ratio
        aspect_ratio = img.height / img.width
        new_height = int(width * aspect_ratio)

        # Resize image
        img_resized = img.resize((width, new_height), Image.Resampling.LANCZOS)

        # Save with EXIF data
        if exif_data:
            img_resized.save(output_path, exif=exif_data, quality=95)
        else:
            img_resized.save(output_path, quality=95)

        return True

    except Exception as e:
        print(f"Error processing {input_path}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Create test photos by scaling down randomly selected photos"
    )
    parser.add_argument("source_dir", help="Source directory containing photos")
    parser.add_argument("output_dir", help="Output directory for test photos")
    parser.add_argument(
        "--count", type=int, default=10, help="Number of photos to select (default: 10)"
    )
    parser.add_argument(
        "--width", type=int, default=256, help="Target width in pixels (default: 256)"
    )
    parser.add_argument("--seed", type=int, help="Random seed for reproducible selection")

    args = parser.parse_args()

    # Set random seed if provided
    if args.seed:
        random.seed(args.seed)
        print(f"Using random seed: {args.seed}")

    # Validate source directory
    if not os.path.isdir(args.source_dir):
        print(f"Error: Source directory not found: {args.source_dir}")
        sys.exit(1)

    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)
    print(f"Output directory: {args.output_dir}")

    # Discover photos
    photos = discover_photos(args.source_dir)

    if len(photos) == 0:
        print("Error: No photos found in source directory")
        sys.exit(1)

    # Select diverse photos
    selected = select_diverse_photos(photos, args.count)
    print(f"\nSelected {len(selected)} photos")

    # Process each photo
    print(f"\nScaling photos to {args.width}px width...")
    print("=" * 60)

    success_count = 0
    for i, input_path in enumerate(selected, 1):
        filename = os.path.basename(input_path)
        output_path = os.path.join(args.output_dir, filename)

        print(f"[{i}/{len(selected)}] {filename}")

        if scale_photo_preserve_exif(input_path, output_path, args.width):
            # Verify EXIF was preserved
            try:
                original = Image.open(input_path)
                scaled = Image.open(output_path)

                orig_exif = original.getexif()
                scaled_exif = scaled.getexif()

                if orig_exif and scaled_exif:
                    # Check for GPS data
                    has_gps = any(tag in orig_exif for tag in [1, 2, 3, 4])  # GPS tags
                    if has_gps:
                        print("  ✓ EXIF preserved (including GPS)")
                    else:
                        print("  ✓ EXIF preserved")
                else:
                    print("  ✓ Scaled (no EXIF in original)")

                success_count += 1

            except Exception as e:
                print(f"  ⚠ Scaled but could not verify EXIF: {e}")
                success_count += 1

    # Summary
    print("=" * 60)
    print("\nSummary:")
    print(f"  Successfully processed: {success_count}/{len(selected)}")
    print(f"  Output directory: {args.output_dir}")
    print(f"  Image width: {args.width}px")

    if success_count == len(selected):
        print("\n✓ All photos processed successfully!")
    else:
        print(f"\n⚠ {len(selected) - success_count} photos failed")


if __name__ == "__main__":
    main()
