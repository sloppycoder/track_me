"""Tests for the Cloudflare Access JWT gate (track_me/viewer/auth.py).

Covers the plain HTTP escape hatch, the track_me-specific fail-open behaviour
when CF_ACCESS_* is unset, and the configured HTTPS path (missing/invalid/valid
token). The JWT verification itself is monkeypatched so no network call or real
signing key is needed.
"""

from __future__ import annotations

import jwt
from flask import Flask

from track_me import config
from track_me.viewer import auth as auth_mod


def _make_client(monkeypatch, *, team_domain="", audience=""):
    """A fresh Flask app with the gate installed and one trivial route.

    init_auth captures the CF config at registration time, so set it before
    building the app.
    """
    monkeypatch.setattr(config, "CF_ACCESS_TEAM_DOMAIN", team_domain)
    monkeypatch.setattr(config, "CF_ACCESS_AUD", audience)

    app = Flask(__name__)
    auth_mod.init_auth(app)

    @app.route("/ping")
    def ping():
        return "pong"

    return app.test_client()


def test_plain_http_is_allowed(monkeypatch):
    """No X-Forwarded-Proto (local dev) -> transparent even when configured."""
    client = _make_client(monkeypatch, team_domain="vino9", audience="aud123")
    assert client.get("/ping").status_code == 200


def test_https_unconfigured_fails_open(monkeypatch):
    """The track_me change: HTTPS + no CF_ACCESS_AUD -> allow, not 403."""
    client = _make_client(monkeypatch, team_domain="", audience="")
    resp = client.get("/ping", headers={"X-Forwarded-Proto": "https"})
    assert resp.status_code == 200


def test_https_aud_ignore_sentinel_fails_open(monkeypatch):
    """CF_ACCESS_AUD='ignore' is the deploy sentinel: configured but off."""
    client = _make_client(monkeypatch, team_domain="vino9", audience="ignore")
    resp = client.get("/ping", headers={"X-Forwarded-Proto": "https"})
    assert resp.status_code == 200


def test_https_configured_without_token_is_denied(monkeypatch):
    client = _make_client(monkeypatch, team_domain="vino9", audience="aud123")
    resp = client.get("/ping", headers={"X-Forwarded-Proto": "https"})
    assert resp.status_code == 403


def test_https_valid_token_is_allowed(monkeypatch):
    monkeypatch.setattr(
        auth_mod, "validate_cf_token", lambda *_a, **_k: {"email": "u@example.com"}
    )
    client = _make_client(monkeypatch, team_domain="vino9", audience="aud123")
    resp = client.get(
        "/ping",
        headers={"X-Forwarded-Proto": "https", "Cf-Access-Jwt-Assertion": "tok"},
    )
    assert resp.status_code == 200
    assert resp.data == b"pong"


def test_https_invalid_token_is_denied(monkeypatch):
    def _boom(*_a, **_k):
        raise jwt.InvalidTokenError("bad")

    monkeypatch.setattr(auth_mod, "validate_cf_token", _boom)
    client = _make_client(monkeypatch, team_domain="vino9", audience="aud123")
    resp = client.get(
        "/ping",
        headers={"X-Forwarded-Proto": "https", "Cf-Access-Jwt-Assertion": "tok"},
    )
    assert resp.status_code == 403


def test_token_from_cookie_is_accepted(monkeypatch):
    """Cloudflare also delivers the JWT via the CF_Authorization cookie."""
    monkeypatch.setattr(
        auth_mod, "validate_cf_token", lambda *_a, **_k: {"email": "u@example.com"}
    )
    client = _make_client(monkeypatch, team_domain="vino9", audience="aud123")
    client.set_cookie("CF_Authorization", "tok")
    resp = client.get("/ping", headers={"X-Forwarded-Proto": "https"})
    assert resp.status_code == 200
