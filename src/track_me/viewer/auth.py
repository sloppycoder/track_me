"""Cloudflare Access (Zero Trust) JWT gate for the viewer.

Ported from the fund_researcher reviewer, adapted to Flask. On each request the
gate verifies the Cloudflare Access JWT — an RS256 token signed by Cloudflare —
against the team's JWKS (fetched once, cached an hour) and checks its ``aud``
claim against ``CF_ACCESS_AUD``.

The gate is transparent when the request is plain HTTP
(``X-Forwarded-Proto`` != ``https``) — local dev; ``track-me serve`` over
``http://localhost`` sends no such header, so the developer is never
challenged.

**Fail-open when unconfigured.** Unlike the fund_researcher original (which
rejects an HTTPS request when auth isn't configured), this gate *allows* the
request when auth is off. Auth is off when ``CF_ACCESS_TEAM_DOMAIN``/
``CF_ACCESS_AUD`` are unset, *or* when ``CF_ACCESS_AUD`` is the literal
``"ignore"`` — a deploy-time sentinel so the workflow can ship the env var
present-but-disabled, and protection is turned on by swapping that one string
for the real Access-app AUD tag.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request

import jwt
from flask import Flask, g, jsonify, request

from track_me import config

logger = logging.getLogger(__name__)

_jwks_cache: dict = {}
_jwks_fetched_at: float = 0.0
_JWKS_TTL = 3600


def _get_jwks(team_domain: str) -> dict:
    """Fetch and cache Cloudflare Access public keys for ``team_domain``."""
    global _jwks_cache, _jwks_fetched_at  # noqa: PLW0603
    if _jwks_cache and time.time() - _jwks_fetched_at < _JWKS_TTL:
        return _jwks_cache
    url = f"https://{team_domain}.cloudflareaccess.com/cdn-cgi/access/certs"
    logger.info("Fetching Cloudflare JWKS from %s", url)
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
        _jwks_cache = json.loads(resp.read())
    _jwks_fetched_at = time.time()
    return _jwks_cache


def validate_cf_token(token: str, team_domain: str, audience: str) -> dict:
    """Validate a Cloudflare Access JWT, returning its decoded claims."""
    keys = jwt.PyJWKSet.from_dict(_get_jwks(team_domain))
    kid = jwt.get_unverified_header(token).get("kid")
    key = next((k for k in keys.keys if k.key_id == kid), None)
    if key is None:
        raise jwt.InvalidTokenError(f"no matching key for kid={kid}")
    return jwt.decode(token, key=key, algorithms=["RS256"], audience=audience)


def init_auth(app: Flask) -> None:
    """Register the Cloudflare Access gate on ``app`` as a ``before_request``."""
    team_domain = config.CF_ACCESS_TEAM_DOMAIN
    audience = config.CF_ACCESS_AUD
    # "ignore" is a deploy sentinel meaning "configured but off" (see module doc).
    enabled = bool(team_domain and audience and audience != "ignore")
    if enabled:
        logger.info("Cloudflare auth enabled for team %s", team_domain)
    else:
        logger.info(
            "Cloudflare auth disabled (CF_ACCESS_AUD unset or 'ignore') — requests allowed"
        )

    @app.before_request
    def _require_cf_jwt():
        if not enabled:
            return None  # fail-open: auth off -> allow
        if request.headers.get("X-Forwarded-Proto", "http") != "https":
            return None  # local dev over HTTP
        token = request.headers.get("Cf-Access-Jwt-Assertion") or request.cookies.get(
            "CF_Authorization"
        )
        if not token:
            return jsonify({"error": "Access denied"}), 403
        try:
            g.user_email = validate_cf_token(token, team_domain, audience).get("email", "")
        except Exception as exc:
            logger.warning("JWT validation failed for %s: %s", request.path, exc)
            return jsonify({"error": "Access denied"}), 403
        return None
