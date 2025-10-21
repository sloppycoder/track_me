import csv
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError

from myphoto.models import Photo


class Command(BaseCommand):
    help = "Validate imported photos against CSV file (checks for missing/mismatched records)"

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=str, help="Path to the CSV file to validate against")

    # ruff: noqa: C901
    def handle(self, *args, **options):
        csv_file = options["csv_file"]

        stats = {
            "total_rows": 0,
            "matched": 0,
            "missing": 0,
            "gps_mismatch": 0,
            "timestamp_mismatch": 0,
        }

        warnings = []

        try:
            with open(csv_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)

                # Validate CSV headers
                required_fields = ["SourceFile", "FileName"]
                if reader.fieldnames is None or not all(
                    field in reader.fieldnames for field in required_fields
                ):
                    raise CommandError(
                        f"CSV missing required fields. Need at least: {required_fields}"
                    )

                self.stdout.write(f"Validating photos from: {csv_file}")
                self.stdout.write("=" * 60)

                for row_num, row in enumerate(reader, start=2):
                    stats["total_rows"] += 1

                    source_file = row.get("SourceFile", "").lstrip("./")
                    file_name = row.get("FileName", "")

                    if not source_file:
                        continue

                    # 1. Check if record exists in database
                    try:
                        photo = Photo.objects.get(source_file=source_file)
                    except Photo.DoesNotExist:
                        stats["missing"] += 1
                        warning = f"Row {row_num}: MISSING - {file_name} (source: {source_file})"
                        warnings.append(warning)
                        self.stdout.write(self.style.WARNING(warning))
                        continue

                    # Record exists, check for mismatches
                    has_mismatch = False

                    # 2. Check GPS coordinates
                    csv_lat = self._parse_decimal(row.get("GPSLatitude", ""))
                    csv_lon = self._parse_decimal(row.get("GPSLongitude", ""))

                    if csv_lat is not None and csv_lon is not None:
                        # CSV has GPS, check if DB has it
                        if photo.gps_latitude is None or photo.gps_longitude is None:
                            stats["gps_mismatch"] += 1
                            warning = (
                                f"Row {row_num}: GPS MISMATCH - {file_name} "
                                f"(CSV has GPS, DB missing)"
                            )
                            warnings.append(warning)
                            self.stdout.write(self.style.WARNING(warning))
                            has_mismatch = True
                        else:
                            # Both have GPS, check if they match (with tolerance)
                            lat_diff = abs(float(photo.gps_latitude) - float(csv_lat))
                            lon_diff = abs(float(photo.gps_longitude) - float(csv_lon))

                            if lat_diff > 0.0001 or lon_diff > 0.0001:
                                stats["gps_mismatch"] += 1
                                warning = (
                                    f"Row {row_num}: GPS MISMATCH - {file_name} "
                                    f"(CSV: {csv_lat}, {csv_lon} vs "
                                    f"DB: {photo.gps_latitude}, {photo.gps_longitude})"
                                )
                                warnings.append(warning)
                                self.stdout.write(self.style.WARNING(warning))
                                has_mismatch = True

                    # 3. Check timestamp (DateTimeOriginal)
                    csv_datetime = row.get("DateTimeOriginal", "").strip()
                    if csv_datetime:
                        db_datetime = (photo.date_time_original_text or "").strip()

                        if csv_datetime != db_datetime:
                            stats["timestamp_mismatch"] += 1
                            warning = (
                                f"Row {row_num}: TIMESTAMP MISMATCH - {file_name} "
                                f"(CSV: '{csv_datetime}' vs DB: '{db_datetime}')"
                            )
                            warnings.append(warning)
                            self.stdout.write(self.style.WARNING(warning))
                            has_mismatch = True

                    # Count as matched if no mismatches
                    if not has_mismatch:
                        stats["matched"] += 1

                    # Progress update every 100 rows
                    if stats["total_rows"] % 100 == 0:
                        self.stdout.write(f"Processed {stats['total_rows']} rows...", ending="\r")

        except FileNotFoundError:
            raise CommandError(f"CSV file not found: {csv_file}")
        except Exception as e:
            raise CommandError(f"Error reading CSV: {e}")

        # Print summary
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS(f"Total rows processed: {stats['total_rows']}"))
        self.stdout.write(self.style.SUCCESS(f"Matched: {stats['matched']}"))

        if stats["missing"] > 0:
            self.stdout.write(self.style.ERROR(f"Missing from DB: {stats['missing']}"))

        if stats["gps_mismatch"] > 0:
            self.stdout.write(self.style.WARNING(f"GPS mismatches: {stats['gps_mismatch']}"))

        if stats["timestamp_mismatch"] > 0:
            self.stdout.write(
                self.style.WARNING(f"Timestamp mismatches: {stats['timestamp_mismatch']}")
            )

        self.stdout.write("=" * 60)

        # Summary
        total_issues = stats["missing"] + stats["gps_mismatch"] + stats["timestamp_mismatch"]
        if total_issues == 0:
            self.stdout.write(
                self.style.SUCCESS("\n✓ All photos validated successfully! No issues found.")
            )
        else:
            self.stdout.write(
                self.style.WARNING(f"\n⚠ Found {total_issues} issues (see warnings above)")
            )

    def _parse_decimal(self, value: str) -> Decimal | None:
        """Parse a decimal value from CSV, return None if invalid."""
        if not value or value.strip() == "":
            return None

        try:
            return Decimal(value.strip())
        except (InvalidOperation, ValueError):
            return None
