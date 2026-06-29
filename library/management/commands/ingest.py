from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from library.ingest.pipeline import IngestPipeline


class Command(BaseCommand):
    help = "Ingest a Google Takeout extract: parse sidecars + EXIF, cache thumbnails"

    def add_arguments(self, parser):
        parser.add_argument(
            "directory",
            nargs="?",
            type=str,
            help="Takeout extract directory (defaults to PHOTOS_BASE_DIR)",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Reprocess items even if already ingested",
        )

    def handle(self, *args, **options):
        directory = options["directory"] or settings.PHOTOS_BASE_DIR
        if not directory:
            raise CommandError("No directory given and PHOTOS_BASE_DIR is unset.")

        self.stdout.write(f"Ingesting from: {directory}")
        if options["force"]:
            self.stdout.write(self.style.WARNING("Force reprocess enabled"))

        pipeline = IngestPipeline(progress_callback=self.stdout.write)
        stats = pipeline.ingest_directory(directory, force=options["force"])

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(f"Total media files: {stats.total_files}")
        self.stdout.write(self.style.SUCCESS(f"Created:  {stats.created}"))
        self.stdout.write(f"Updated:  {stats.updated}")
        self.stdout.write(f"Skipped:  {stats.skipped}")
        self.stdout.write(f"Sidecars matched: {stats.with_sidecar}")
        self.stdout.write(
            f"Located: {stats.with_location}   No location: {stats.without_location}"
        )
        if stats.errors:
            self.stdout.write(self.style.ERROR(f"Errors: {stats.errors}"))
            for detail in stats.error_details[:10]:
                self.stderr.write(f"  {detail}")
        self.stdout.write("=" * 60)
