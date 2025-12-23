from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from myphoto.services.photo_processing_service import PhotoProcessingService


class Command(BaseCommand):
    help = (
        "Process photo files from a directory "
        "(extract EXIF, GPS, calculate H3 indexes and perceptual hashes)"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "directory",
            nargs="?",
            type=str,
            help=(
                "Top-level directory containing photo files "
                "(defaults to PHOTOS_BASE_DIR from settings)"
            ),
        )
        parser.add_argument(
            "--force-reprocess",
            action="store_true",
            help="Reprocess photos even if already processed",
        )

    def handle(self, *args, **options):
        directory = options["directory"]
        force_reprocess = options["force_reprocess"]

        # If directory not provided, use PHOTOS_BASE_DIR from settings
        if not directory:
            directory = settings.PHOTOS_BASE_DIR
            if not directory:
                raise CommandError(
                    "No directory specified. Either provide a directory argument "
                    "or set PHOTOS_BASE_DIR in settings/environment."
                )
            self.stdout.write(f"Using PHOTOS_BASE_DIR from settings: {directory}")

        self.stdout.write(f"Processing photos from: {directory}")
        if force_reprocess:
            self.stdout.write(self.style.WARNING("Force reprocess enabled"))

        # Create service
        service = PhotoProcessingService(progress_callback=self.stdout.write)

        # Process directory
        stats = service.process_directory(
            directory_path=directory,
            force_reprocess=force_reprocess,
        )

        # Display results
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(f"Total files found: {stats['total_files']}")

        if stats["created"] > 0:
            self.stdout.write(self.style.SUCCESS(f"Created {stats['created']} new photo records"))
        if stats["updated"] > 0:
            self.stdout.write(
                self.style.SUCCESS(f"Updated {stats['updated']} existing photo records")
            )
        if stats["skipped"] > 0:
            self.stdout.write(
                self.style.WARNING(f"Skipped {stats['skipped']} already processed photos")
            )
        if stats["errors"] > 0:
            self.stdout.write(self.style.ERROR(f"Errors: {stats['errors']}"))
            # Show first few errors
            for error in stats["error_details"][:5]:
                self.stderr.write(f"  {error}")

        self.stdout.write("=" * 60)
