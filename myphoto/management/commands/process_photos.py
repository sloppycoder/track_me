from django.core.management.base import BaseCommand

from myphoto.services.photo_processing_service import PhotoProcessingService


class Command(BaseCommand):
    help = (
        "Process photo files from a directory "
        "(extract EXIF, GPS, calculate H3 indexes and perceptual hashes)"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "directory",
            type=str,
            help="Top-level directory containing photo files",
        )
        parser.add_argument(
            "--force-reprocess",
            action="store_true",
            help="Reprocess photos even if already processed",
        )

    def handle(self, *args, **options):
        directory = options["directory"]
        force_reprocess = options["force_reprocess"]

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
