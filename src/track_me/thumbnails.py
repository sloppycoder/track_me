"""Generate and cache thumbnails, content-addressed by ``dedupe_key``.

Keyed by content identity rather than DB id so the cache survives a schema/DB
reset, and generated eagerly at ingest so it keeps working after the (transient)
Takeout extract is deleted -- the original is then viewed via the Google Photos
deep link.
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

import pillow_heif
from PIL import Image

pillow_heif.register_heif_opener()

logger = logging.getLogger(__name__)


class ThumbnailService:
    def __init__(self, cache_dir: Path, size: tuple[int, int]):
        self.cache_dir = Path(cache_dir)
        self.size = size
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, dedupe_key: str) -> Path:
        return self.cache_dir / f"{dedupe_key}.jpg"

    def exists(self, dedupe_key: str) -> bool:
        return self.path_for(dedupe_key).exists()

    def generate(self, source_path: Path, dedupe_key: str, *, force: bool = False) -> Path | None:
        """Create (or reuse) the cached thumbnail. Returns its path, or None."""
        dest = self.path_for(dedupe_key)
        if dest.exists() and not force:
            return dest
        if not source_path.exists():
            logger.warning("Cannot thumbnail; source missing: %s", source_path)
            return None

        try:
            with Image.open(source_path) as img:
                img = self._flatten(img)
                img.thumbnail(self.size, Image.Resampling.LANCZOS)
                img.save(dest, "JPEG", quality=85, optimize=True)
        except Exception as e:
            logger.warning("Thumbnail failed for %s: %s", source_path, e)
            return None
        return dest

    def generate_from_bytes(
        self, data: bytes, dedupe_key: str, *, force: bool = False
    ) -> Path | None:
        """Create (or reuse) the cached thumbnail from raw image bytes."""
        dest = self.path_for(dedupe_key)
        if dest.exists() and not force:
            return dest
        try:
            with Image.open(BytesIO(data)) as img:
                img = self._flatten(img)
                img.thumbnail(self.size, Image.Resampling.LANCZOS)
                img.save(dest, "JPEG", quality=85, optimize=True)
        except Exception as e:
            logger.warning("Thumbnail failed for %s: %s", dedupe_key, e)
            return None
        return dest

    @staticmethod
    def _flatten(img: Image.Image) -> Image.Image:
        """Composite transparency onto white so it can be saved as JPEG."""
        if img.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            mask = img.split()[-1] if img.mode == "RGBA" else None
            background.paste(img, mask=mask)
            return background
        if img.mode != "RGB":
            return img.convert("RGB")
        return img
