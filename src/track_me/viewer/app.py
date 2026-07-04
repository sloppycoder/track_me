"""Minimal, Django-free timeline map viewer.

Serves the static map UI and injects the Google Maps API key (from ``.env``)
server-side, so the key never lives in the browser file or the repo. Timeline
data files (``userdata/timelines/<id>.json``) are produced by the
timeline-building agent and served as-is for the page's JS to fetch.

Run:
    track-me serve              # then open http://localhost:5000

Routes:
    GET /                    index of every userdata/timelines/*.json
    GET /t/<id>              render the map for one timeline
    GET /timeline/<id>.json  raw timeline data (fetched by the page JS)
"""

from __future__ import annotations

import json
import os

from flask import Flask, abort, render_template, send_from_directory

from track_me import config

TIMELINES_DIR = config.TIMELINES_DIR

app = Flask(__name__)


@app.after_request
def _no_cache(resp):
    """Cache-bust everything this server hands out (HTML + inline CSS/JS + the
    timeline JSON) so edits and freshly rebuilt timelines always show up on
    reload. The Google Maps JS/CSS is loaded straight from the CDN by the
    browser — it never passes through here — so it stays cacheable."""
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


def _api_key() -> str:
    key = config.GOOGLE_MAPS_API_KEY
    if not key:
        raise RuntimeError("GOOGLE_MAPS_API_KEY not set (put it in .env).")
    return key


def _list_timelines() -> list[dict]:
    """Return light metadata for every timeline file, newest first."""
    out: list[dict] = []
    for path in sorted(TIMELINES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        out.append(
            {
                "id": path.stem,
                "title": data.get("title", path.stem),
                "generated_at": data.get("generated_at", ""),
                "stay_count": len(data.get("stays", [])),
            }
        )
    out.sort(key=lambda t: t["generated_at"], reverse=True)
    return out


@app.route("/")
def index():
    return render_template("index.html", timelines=_list_timelines())


@app.route("/t/<timeline_id>")
def view(timeline_id: str):
    if not (TIMELINES_DIR / f"{timeline_id}.json").is_file():
        abort(404)
    return render_template("map.html", timeline_id=timeline_id, api_key=_api_key())


@app.route("/timeline/<timeline_id>.json")
def timeline_data(timeline_id: str):
    # send_from_directory guards against path traversal in <timeline_id>.
    return send_from_directory(TIMELINES_DIR, f"{timeline_id}.json", mimetype="application/json")


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("VIEWER_PORT", "5000")))
