from django.core.management.base import BaseCommand

from library.ingest.pipeline import IngestPipeline


class Command(BaseCommand):
    help = "Ingest a Google Takeout extract: parse sidecars + EXIF, geotag (thumbnails opt-in)"

    def add_arguments(self, parser):
        parser.add_argument(
            "directory",
            type=str,
            help="Takeout extract directory to ingest (changes per incremental export)",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Reprocess items even if already ingested",
        )
        parser.add_argument(
            "--thumbnails",
            action="store_true",
            help="Also generate thumbnails (off by default; timeline data doesn't need them)",
        )

    def handle(self, *args, **options):
        directory = options["directory"]
        self.stdout.write(f"Ingesting from: {directory}")
        if options["force"]:
            self.stdout.write(self.style.WARNING("Force reprocess enabled"))
        if options["thumbnails"]:
            self.stdout.write("Thumbnails enabled")

        pipeline = IngestPipeline(
            progress_callback=self.stdout.write,
            generate_thumbnails=options["thumbnails"],
        )
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
