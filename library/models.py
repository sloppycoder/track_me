"""Core catalog model.

A single, lean ``MediaItem`` row per photo/video, content-addressed so it is
stable across overlapping/incremental Google Takeout dumps and DB resets.
"""

from decimal import Decimal

import h3
from django.db import models

# The single finest H3 resolution we persist. Coarser cells (neighbourhood /
# metro / region) are derived on demand with ``h3.cell_to_parent`` -- no need to
# store five columns like the old schema did.
H3_BASE_RESOLUTION = 11


class TimeSource(models.TextChoices):
    SIDECAR = "sidecar", "Takeout sidecar"
    EXIF = "exif", "EXIF"
    FILE_MTIME = "file_mtime", "File mtime"
    MANUAL = "manual", "Manual"


class LocationSource(models.TextChoices):
    EXIF_GPS = "exif_gps", "EXIF GPS"
    TAKEOUT = "takeout_geodata", "Takeout geoData"
    MANUAL = "manual", "Manual"
    INTERPOLATED = "interpolated", "Interpolated"
    NONE = "none", "None"


class MediaKind(models.TextChoices):
    PHOTO = "photo", "Photo"
    VIDEO = "video", "Video"


class MediaItem(models.Model):
    """One photo or video, merged from Takeout sidecar + EXIF."""

    # --- identity (stable across Takeout dumps) ---------------------------
    dedupe_key = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="Stable content identity (URL slug / title+time / phash hash)",
    )
    google_photos_url = models.URLField(
        max_length=512,
        null=True,
        blank=True,
        help_text="Deep link back to the original on photos.google.com",
    )

    # --- file / kind -----------------------------------------------------
    file_name = models.CharField(max_length=255)
    kind = models.CharField(max_length=10, choices=MediaKind.choices, default=MediaKind.PHOTO)
    last_source_path = models.CharField(
        max_length=512,
        blank=True,
        help_text="Most recent path this item was seen at in a Takeout (info only)",
    )

    # --- time (authoritative, tz-aware; set for EVERY item at ingest) -----
    taken_at = models.DateTimeField(null=True, blank=True, db_index=True)
    time_source = models.CharField(
        max_length=12, choices=TimeSource.choices, null=True, blank=True
    )

    # --- location --------------------------------------------------------
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    h3_cell = models.CharField(
        max_length=16,
        null=True,
        blank=True,
        db_index=True,
        help_text=f"H3 cell at resolution {H3_BASE_RESOLUTION}; parents derived on demand",
    )
    location_source = models.CharField(
        max_length=16,
        choices=LocationSource.choices,
        default=LocationSource.NONE,
        db_index=True,
    )

    # --- geocoded names (filled later by the places app) -----------------
    place_label = models.CharField(max_length=255, blank=True)
    country_code = models.CharField(max_length=2, null=True, blank=True, db_index=True)
    geocoded_at = models.DateTimeField(null=True, blank=True)

    # --- media handling --------------------------------------------------
    thumbnail_cached_at = models.DateTimeField(null=True, blank=True)
    perceptual_hash = models.CharField(max_length=16, null=True, blank=True, db_index=True)

    # --- review + provenance ---------------------------------------------
    needs_review = models.BooleanField(default=False, db_index=True)
    sidecar_raw = models.JSONField(null=True, blank=True)
    exif = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "media_item"
        ordering = ["-taken_at", "file_name"]
        indexes = [
            models.Index(fields=["taken_at"]),
            models.Index(fields=["location_source", "needs_review"]),
            models.Index(fields=["country_code"]),
        ]

    def __str__(self) -> str:
        when = self.taken_at.isoformat() if self.taken_at else "unknown time"
        return f"{self.file_name} ({when})"

    # --- helpers ---------------------------------------------------------
    @property
    def has_location(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    def set_location(self, lat: float, lon: float, source: str) -> None:
        """Set coordinates, recompute the H3 cell, and tag the source."""
        self.latitude = Decimal(str(lat))
        self.longitude = Decimal(str(lon))
        self.h3_cell = h3.latlng_to_cell(lat, lon, H3_BASE_RESOLUTION)
        self.location_source = source

    def clear_location(self) -> None:
        self.latitude = None
        self.longitude = None
        self.h3_cell = None
        self.location_source = LocationSource.NONE

    def h3_at(self, resolution: int) -> str | None:
        """Derive the parent H3 cell at a coarser resolution from ``h3_cell``."""
        if not self.h3_cell:
            return None
        if resolution >= H3_BASE_RESOLUTION:
            return self.h3_cell
        return h3.cell_to_parent(self.h3_cell, resolution)
