"""Tests for the Cloudflare Access JWT gate (track_me/viewer/auth.py).

Covers the loopback-host local-dev escape hatch, the track_me-specific fail-open
behaviour when CF_ACCESS_* is unset, and the configured production path
(missing/invalid/valid token). The JWT verification itself is monkeypatched so no
network call or real signing key is needed.

Requests to a loopback Host (the flask test client's default is ``localhost``) are
treated as local dev and skip the gate, so the "configured" tests use ``PROD_URL``
as their ``base_url`` to simulate a real, non-loopback deployment host.
"""

from __future__ import annotations

import jwt
from flask import Flask

from track_me import config
from track_me.viewer import auth as auth_mod

PROD_URL = "https://viewer.example.com"


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


def test_loopback_host_is_allowed(monkeypatch):
    """Loopback Host (the test client's default localhost) -> local dev, no gate."""
    client = _make_client(monkeypatch, team_domain="vino9", audience="aud123")
    assert client.get("/ping").status_code == 200


def test_spoofed_forwarded_proto_does_not_bypass(monkeypatch):
    """A forged X-Forwarded-Proto must NOT open the gate on a real (prod) host."""
    client = _make_client(monkeypatch, team_domain="vino9", audience="aud123")
    resp = client.get("/ping", base_url=PROD_URL, headers={"X-Forwarded-Proto": "http"})
    assert resp.status_code == 403


def test_unconfigured_fails_open(monkeypatch):
    """The track_me change: prod host + no CF_ACCESS_AUD -> allow, not 403."""
    client = _make_client(monkeypatch, team_domain="", audience="")
    resp = client.get("/ping", base_url=PROD_URL)
    assert resp.status_code == 200


def test_aud_ignore_sentinel_fails_open(monkeypatch):
    """CF_ACCESS_AUD='ignore' is the deploy sentinel: configured but off."""
    client = _make_client(monkeypatch, team_domain="vino9", audience="ignore")
    resp = client.get("/ping", base_url=PROD_URL)
    assert resp.status_code == 200


def test_configured_without_token_is_denied(monkeypatch):
    client = _make_client(monkeypatch, team_domain="vino9", audience="aud123")
    resp = client.get("/ping", base_url=PROD_URL)
    assert resp.status_code == 403


def test_valid_token_is_allowed(monkeypatch):
    monkeypatch.setattr(
        auth_mod, "validate_cf_token", lambda *_a, **_k: {"email": "u@example.com"}
    )
    client = _make_client(monkeypatch, team_domain="vino9", audience="aud123")
    resp = client.get("/ping", base_url=PROD_URL, headers={"Cf-Access-Jwt-Assertion": "tok"})
    assert resp.status_code == 200
    assert resp.data == b"pong"


def test_invalid_token_is_denied(monkeypatch):
    def _boom(*_a, **_k):
        raise jwt.InvalidTokenError("bad")

    monkeypatch.setattr(auth_mod, "validate_cf_token", _boom)
    client = _make_client(monkeypatch, team_domain="vino9", audience="aud123")
    resp = client.get("/ping", base_url=PROD_URL, headers={"Cf-Access-Jwt-Assertion": "tok"})
    assert resp.status_code == 403


def test_token_from_cookie_is_accepted(monkeypatch):
    """Cloudflare also delivers the JWT via the CF_Authorization cookie."""
    monkeypatch.setattr(
        auth_mod, "validate_cf_token", lambda *_a, **_k: {"email": "u@example.com"}
    )
    client = _make_client(monkeypatch, team_domain="vino9", audience="aud123")
    client.set_cookie("CF_Authorization", "tok", domain="viewer.example.com")
    resp = client.get("/ping", base_url=PROD_URL)
    assert resp.status_code == 200
