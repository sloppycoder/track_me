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
# Resolved to an absolute path: Flask's send_from_directory (the viewer's
# /timeline/<id>.json route) resolves a *relative* dir against the package
# root_path, not the CWD, so a relative userdata dir would 404 there while the
# CWD-relative .glob() index still found the files. Absolute keeps them in sync.
USERDATA_DIR = Path(
    os.getenv("TRACKME_USERDATA") or os.getenv("USERDATA_DIR") or "userdata"
).resolve()

DB_PATH = Path(os.getenv("DB_PATH", str(USERDATA_DIR / "track_me.db")))
# The viewer opens the DB read-only (it never writes). Set DB_IMMUTABLE=1 to also
# open it immutable — SQLite then skips locking + WAL/SHM sidecars, a win when the
# DB lives on a network filesystem (e.g. gcsfuse on Cloud Run). Only safe if nothing
# rewrites the file while the app runs, so keep it OFF for local dev where an
# ingest/geocode may run alongside `track-me serve`.
DB_IMMUTABLE = os.getenv("DB_IMMUTABLE") == "1"
# Preserved copy of the old Django DB, kept for the old-vs-new comparison.
LEGACY_DB_PATH = Path(os.getenv("LEGACY_DB_PATH", str(USERDATA_DIR / "track_me_legacy.db")))

THUMBNAIL_CACHE_DIR = Path(os.getenv("THUMBNAIL_CACHE_DIR", str(USERDATA_DIR / "thumbnails")))
THUMBNAIL_SIZE = (
    int(os.getenv("THUMBNAIL_WIDTH", "300")),
    int(os.getenv("THUMBNAIL_HEIGHT", "200")),
)
# .resolve() so a relative TIMELINES_DIR override still lands as absolute (see
# the USERDATA_DIR note above — this dir is served via Flask send_from_directory).
TIMELINES_DIR = Path(os.getenv("TIMELINES_DIR", str(USERDATA_DIR / "timelines"))).resolve()

# --- ingest / geocode ----------------------------------------------------
PHOTOS_BASE_DIR = Path(os.path.expanduser(os.getenv("PHOTOS_BASE_DIR", "~/tmp")))
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

# --- MCP server (Google OAuth) ------------------------------------------
# `track-me mcp` serves read-only catalog tools to chat clients (ChatGPT,
# Claude). Over HTTP it requires Google sign-in: MCP_BASE_URL is the public URL
# (the Google OAuth redirect URI is MCP_BASE_URL + "/auth/callback"), and only
# the comma-separated MCP_ALLOWED_EMAILS may connect. MCP_JWT_SIGNING_KEY signs
# the tokens the server hands to clients; unset, FastMCP derives it from the
# client secret. OAuth state (client registrations, tokens) lives under
# FASTMCP_HOME — point it at persistent storage so restarts keep clients signed in.
MCP_BASE_URL = os.getenv("MCP_BASE_URL", "")
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
MCP_ALLOWED_EMAILS = frozenset(
    e.strip().lower() for e in os.getenv("MCP_ALLOWED_EMAILS", "").split(",") if e.strip()
)
MCP_JWT_SIGNING_KEY = os.getenv("MCP_JWT_SIGNING_KEY") or None


def ensure_dirs() -> None:
    """Create the userdata directories if missing (call before writing)."""
    for d in (USERDATA_DIR, THUMBNAIL_CACHE_DIR, TIMELINES_DIR):
        d.mkdir(parents=True, exist_ok=True)
