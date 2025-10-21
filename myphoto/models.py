import h3
from django.db import models


class Photo(models.Model):
    """Model to store photo metadata from Google Photos export."""

    # Basic metadata
    source_file = models.CharField(max_length=500, help_text="Original file path from export")
    file_name = models.CharField(max_length=255, help_text="Photo file name")
    directory = models.CharField(max_length=500, help_text="Directory path in export")

    # Original datetime as text (as extracted from EXIF)
    date_time_original_text = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="Original photo timestamp as text from EXIF",
    )

    # Timezone-aware datetime (calculated later)
    date_time_taken = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timezone-aware datetime when photo was taken (calculated)",
    )

    # Original GPS coordinates from EXIF
    gps_latitude = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="GPS latitude from EXIF",
    )
    gps_longitude = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="GPS longitude from EXIF",
    )
    gps_altitude = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="GPS altitude in meters from EXIF",
    )

    # Clustered GPS coordinates (for location grouping)
    cluster_latitude = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Clustered latitude (photos within ~10km radius)",
    )
    cluster_longitude = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        null=True,
        blank=True,
        help_text="Clustered longitude (photos within ~10km radius)",
    )

    # Geocoded location information
    location = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text="Human-readable location name from geocoding",
    )
    country_code = models.CharField(
        max_length=2, null=True, blank=True, help_text="ISO 3166-1 alpha-2 country code"
    )
    geo_coded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When geocoding was last performed for this photo",
    )

    # H3 spatial indexes for hierarchical visualization
    h3_res_15 = models.CharField(
        max_length=15,
        null=True,
        blank=True,
        db_index=True,
        help_text="H3 resolution 15 (~0.9m²) - street level",
    )
    h3_res_12 = models.CharField(
        max_length=15,
        null=True,
        blank=True,
        db_index=True,
        help_text="H3 resolution 12 (~0.3km²) - neighborhood level",
    )
    h3_res_9 = models.CharField(
        max_length=15,
        null=True,
        blank=True,
        db_index=True,
        help_text="H3 resolution 9 (~11km²) - city level",
    )
    h3_res_6 = models.CharField(
        max_length=15,
        null=True,
        blank=True,
        db_index=True,
        help_text="H3 resolution 6 (~290km²) - region level",
    )
    h3_res_3 = models.CharField(
        max_length=15,
        null=True,
        blank=True,
        db_index=True,
        help_text="H3 resolution 3 (~12,000km²) - country level",
    )

    # Perceptual hashes for duplicate detection (works across resolutions)
    perceptual_hash = models.CharField(
        max_length=16,
        null=True,
        blank=True,
        db_index=True,
        help_text="pHash for finding duplicates (works across resolutions)",
    )
    average_hash = models.CharField(
        max_length=16,
        null=True,
        blank=True,
        db_index=True,
        help_text="aHash for finding similar images (faster, less accurate)",
    )
    difference_hash = models.CharField(
        max_length=16,
        null=True,
        blank=True,
        db_index=True,
        help_text="dHash for finding similar images (gradient-based)",
    )

    # Full EXIF metadata (stored as JSON)
    exif_meta = models.JSONField(
        null=True,
        blank=True,
        help_text="Complete EXIF metadata extracted from photo file",
    )

    # Timestamps
    imported_at = models.DateTimeField(
        auto_now=True, help_text="Last time this photo was processed/imported"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "photos"
        indexes = [
            models.Index(fields=["date_time_taken"]),
            models.Index(fields=["file_name"]),
            models.Index(fields=["gps_latitude", "gps_longitude"]),
            models.Index(fields=["cluster_latitude", "cluster_longitude"]),
            models.Index(fields=["country_code"]),
            models.Index(fields=["location"]),
        ]
        # Prevent duplicate imports based on source file path
        constraints = [models.UniqueConstraint(fields=["source_file"], name="unique_source_file")]
        ordering = ["-date_time_taken", "-date_time_original_text"]

    def __str__(self):
        dt = self.date_time_taken or self.date_time_original_text or "Unknown date"
        return f"{self.file_name} ({dt})"

    @property
    def has_gps(self):
        """Check if photo has GPS coordinates from EXIF."""
        return self.gps_latitude is not None and self.gps_longitude is not None

    @property
    def has_cluster_gps(self):
        """Check if photo has clustered GPS coordinates."""
        return self.cluster_latitude is not None and self.cluster_longitude is not None

    @property
    def has_location(self):
        """Check if photo has geocoded location information."""
        return self.location is not None or self.country_code is not None

    @property
    def has_h3_indexes(self):
        """Check if photo has H3 spatial indexes calculated."""
        return self.h3_res_9 is not None

    @property
    def has_perceptual_hash(self):
        """Check if photo has perceptual hash calculated."""
        return self.perceptual_hash is not None

    def calculate_h3_indexes(self):
        """Calculate H3 indexes at multiple resolutions from GPS coordinates."""
        if not self.has_gps:
            return False

        lat = float(self.gps_latitude)
        lon = float(self.gps_longitude)

        # Calculate H3 indexes at different resolutions
        self.h3_res_15 = h3.latlng_to_cell(lat, lon, 15)
        self.h3_res_12 = h3.latlng_to_cell(lat, lon, 12)
        self.h3_res_9 = h3.latlng_to_cell(lat, lon, 9)
        self.h3_res_6 = h3.latlng_to_cell(lat, lon, 6)
        self.h3_res_3 = h3.latlng_to_cell(lat, lon, 3)

        return True

    def get_h3_center(self, resolution=9):
        """Get the center coordinates of the H3 cell at given resolution."""
        h3_field = f"h3_res_{resolution}"
        h3_index = getattr(self, h3_field, None)
        if h3_index:
            lat, lon = h3.cell_to_latlng(h3_index)
            return lat, lon
        return None
