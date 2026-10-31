"""Configuration for the Django-free track_me tool.

Reads ``.env`` (via python-dotenv) into plain module constants. No Django, no
cloud/deploy keys. All local state — the SQLite DB, thumbnails — lives
under ``userdata/`` (gitignored).
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# repo root = three levels up from src/track_me/config.py
BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(dotenv_path=BASE_DIR / ".env")

# Single local-state root: DB + generated output (thumbnails).
# Override with TRACKME_USERDATA (default ./userdata, relative to the CWD).
# The older USERDATA_DIR name is still honored as a fallback.
USERDATA_DIR = Path(
    os.getenv("TRACKME_USERDATA") or os.getenv("USERDATA_DIR") or "userdata"
).resolve()

DB_PATH = Path(os.getenv("DB_PATH", str(USERDATA_DIR / "track_me.db")))

THUMBNAIL_CACHE_DIR = Path(os.getenv("THUMBNAIL_CACHE_DIR", str(USERDATA_DIR / "thumbnails")))
THUMBNAIL_SIZE = (
    int(os.getenv("THUMBNAIL_WIDTH", "300")),
    int(os.getenv("THUMBNAIL_HEIGHT", "200")),
)

# --- ingest / geocode ----------------------------------------------------
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

# --- MCP server (Google OAuth) ------------------------------------------
# `track-me mcp` serves read-only catalog tools to chat clients (ChatGPT,
# Claude). Over HTTP it requires Google sign-in: MCP_BASE_URL is the public URL
# (the Google OAuth redirect URI is MCP_BASE_URL + "/auth/callback"), and only
# the comma-separated MCP_ALLOWED_EMAILS may connect. MCP_JWT_SIGNING_KEY signs
# the tokens the server hands to clients; unset, FastMCP derives it from the
# client secret. OAuth state (client registrations, tokens) lives under
# FASTMCP_HOME — on Cloud Run it points at the mounted bucket so scale-to-zero keeps
# clients signed in; on ephemeral disk clients must sign in again after a restart.
#
# TRACKME_DB_URI (gs://bucket/key): at startup `track-me mcp` copies the catalog
# there to TRACKME_DB_LOCAL (default /tmp/track_me.db — in-memory on Cloud Run) and
# serves from the copy, so queries never hit object storage.
DB_GCS_URI = os.getenv("TRACKME_DB_URI", "")
DB_LOCAL_COPY = Path(os.getenv("TRACKME_DB_LOCAL", "/tmp/track_me.db"))
MCP_BASE_URL = os.getenv("MCP_BASE_URL", "")
GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
MCP_ALLOWED_EMAILS = frozenset(
    e.strip().lower() for e in os.getenv("MCP_ALLOWED_EMAILS", "").split(",") if e.strip()
)
MCP_JWT_SIGNING_KEY = os.getenv("MCP_JWT_SIGNING_KEY") or None


def ensure_dirs() -> None:
    """Create the userdata directories if missing (call before writing)."""
    for d in (USERDATA_DIR, THUMBNAIL_CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)
