"""Minimal, Django-free timeline map viewer.

Serves the static map UI and injects the Google Maps API key (from ``.env``)
server-side, so the key never lives in the browser file or the repo. Timeline
data files (``userdata/timelines/<id>.json``) are produced by the web builder
(``/build``) or the ``track-me timeline`` CLI and served as-is for the page's JS
to fetch.

Run:
    track-me serve              # then open http://localhost:5000

Routes:
    GET  /                    index of every userdata/timelines/*.json
    GET  /t/<id>              render the map for one timeline
    GET  /timeline/<id>.json  raw timeline data (fetched by the page JS)
    GET  /build               the timeline builder form (+ /build/<id> to edit)
    GET  /api/range           catalog date span + country codes for the form
    GET  /api/preview         stays for one set of knobs (live preview)
    POST /api/timeline        rebuild + persist a timeline, return its /t/<id>

The write route (POST /api/timeline) has no auth; the server binds 127.0.0.1 by
default (Flask's ``app.run`` default host), so it is not exposed off-box.
"""

from __future__ import annotations

import json
import os
import re

from flask import Flask, abort, jsonify, render_template, request, send_from_directory

from track_me import config
from track_me import timeline as tl
from track_me.db import Database
from track_me.viewer.auth import init_auth

TIMELINES_DIR = config.TIMELINES_DIR

# Filename-safe timeline ids only (also blocks path traversal on the write path,
# which — unlike the read routes — has no send_from_directory guard).
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

app = Flask(__name__)
# Gate every request behind Cloudflare Access when CF_ACCESS_* is configured;
# fail-open (allow) when it isn't, so local dev and unconfigured deploys work.
init_auth(app)


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


# --------------------------------------------------------------------------- #
# Timeline builder                                                            #
# --------------------------------------------------------------------------- #
def _load_build_block(timeline_id: str) -> dict | None:
    """The stored knob values for an existing timeline, if any, so /build/<id>
    can prefill the form. None if the file is missing/unreadable."""
    path = TIMELINES_DIR / f"{timeline_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    block = data.get("build")
    if block:
        block = {**block, "id": timeline_id, "title": data.get("title", timeline_id)}
    return block


@app.route("/build")
@app.route("/build/<timeline_id>")
def build(timeline_id: str | None = None):
    # The builder is a plain form + a server-computed stats summary — no map —
    # so it needs no Google Maps key (the interactive map lives on /t/<id>).
    prefill = _load_build_block(timeline_id) if timeline_id else None
    return render_template("build.html", build=prefill)


@app.route("/api/range")
def api_range():
    """Catalog date span + distinct country codes — feeds the builder form's
    date-picker bounds and region chips (no heavy points payload)."""
    db = Database(config.DB_PATH)
    db.init_schema()
    rows = db.located_with_place()  # time-ordered by taken_at
    if not rows:
        return jsonify({"min": None, "max": None, "countries": []})
    countries = sorted({r["country_code"] for r in rows if r["country_code"]})
    return jsonify(
        {
            "min": rows[0]["taken_at"][:10],
            "max": rows[-1]["taken_at"][:10],
            "countries": countries,
        }
    )


def _build_stays_from_args(src) -> tuple[list[dict], list[dict], dict]:
    """Shared load+build for /api/preview and POST /api/timeline. Returns
    (points, stays, knobs) mirroring cli._cmd_timeline exactly."""
    start = src.get("start")
    end = src.get("end")
    level = src.get("level") or "country"
    region = src.getlist("region") if hasattr(src, "getlist") else src.get("region")
    region = region or None
    merge_km = float(src.get("merge_km", 50.0) or 50.0)
    min_hours = int(src.get("min_hours", 24) or 24)

    db = Database(config.DB_PATH)
    db.init_schema()
    points = tl.load_points(start, end, db=db, region=region)
    stays = tl.build_stays(
        start,
        end,
        level=level,
        region=region,
        merge_km=merge_km,
        min_hours=min_hours,
        db=db,
        points=points,
    )
    knobs = {
        "start": start,
        "end": end,
        "level": level,
        "region": region,
        "merge_km": merge_km,
        "min_hours": min_hours,
    }
    return points, stays, knobs


@app.route("/api/preview")
def api_preview():
    if not request.args.get("start") or not request.args.get("end"):
        return jsonify({"error": "start and end required"}), 400
    _, stays, _ = _build_stays_from_args(request.args)
    return jsonify({"stays": stays, "stay_count": len(stays)})


@app.route("/api/timeline", methods=["POST"])
def api_timeline():
    body = request.get_json(silent=True) or {}
    timeline_id = (body.get("id") or "").strip()
    title = (body.get("title") or "").strip()
    if not _ID_RE.match(timeline_id):
        return jsonify({"ok": False, "error": "invalid id"}), 400
    if not title:
        return jsonify({"ok": False, "error": "title required"}), 400
    if not body.get("start") or not body.get("end"):
        return jsonify({"ok": False, "error": "start and end required"}), 400

    points, stays, knobs = _build_stays_from_args(body)
    doc = tl.to_document(
        stays,
        timeline_id=timeline_id,
        title=title,
        prompts=body.get("prompts") or [],
        points=points if body.get("embed_points", True) else None,
        build=knobs,
    )
    tl.write_timeline(doc)
    return jsonify({"ok": True, "id": timeline_id, "url": f"/t/{timeline_id}"})


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("VIEWER_PORT", "5000")))
