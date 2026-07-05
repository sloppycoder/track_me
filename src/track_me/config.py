"""Configuration for the Django-free track_me tool.

Reads ``.env`` (via python-dotenv) into plain module constants. No Django, no
cloud/deploy keys. All local state — the SQLite DB, thumbnails, timelines — lives
under ``userdata/`` (gitignored).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# repo root = three levels up from src/track_me/config.py
BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(dotenv_path=BASE_DIR / ".env")

# Single local-state root: DB + generated output (thumbnails, timelines).
# Override with TRACKME_USERDATA (default ./userdata, relative to the CWD).
# The older USERDATA_DIR name is still honored as a fallback.
USERDATA_DIR = Path(os.getenv("TRACKME_USERDATA") or os.getenv("USERDATA_DIR") or "userdata")

DB_PATH = Path(os.getenv("DB_PATH", str(USERDATA_DIR / "track_me.db")))
# Preserved copy of the old Django DB, kept for the old-vs-new comparison.
LEGACY_DB_PATH = Path(os.getenv("LEGACY_DB_PATH", str(USERDATA_DIR / "track_me_legacy.db")))

THUMBNAIL_CACHE_DIR = Path(os.getenv("THUMBNAIL_CACHE_DIR", str(USERDATA_DIR / "thumbnails")))
THUMBNAIL_SIZE = (
    int(os.getenv("THUMBNAIL_WIDTH", "300")),
    int(os.getenv("THUMBNAIL_HEIGHT", "200")),
)
TIMELINES_DIR = Path(os.getenv("TIMELINES_DIR", str(USERDATA_DIR / "timelines")))

# --- ingest / geocode ----------------------------------------------------
PHOTOS_BASE_DIR = Path(os.path.expanduser(os.getenv("PHOTOS_BASE_DIR", "~/tmp")))
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")


def ensure_dirs() -> None:
    """Create the userdata directories if missing (call before writing)."""
    for d in (USERDATA_DIR, THUMBNAIL_CACHE_DIR, TIMELINES_DIR):
        d.mkdir(parents=True, exist_ok=True)
