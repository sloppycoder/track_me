# Fronting apps under one hostname with Cloudflare (path routing + Zero Trust)

This directory holds the Cloudflare edge config that puts the track_me viewer —
and any other personal app you add later — behind **`lee.vino9.net/<path>/`**,
gated by **Cloudflare Access (Zero Trust)**, with **one** DNS record, **one**
cert, **one** Worker, and **one** Access application for the whole fleet.

```
Browser → lee.vino9.net/trackme/*
   │
   ├─ ① Cloudflare Access   whole-host app "lee" (lee.vino9.net/*) → login → CF_Authorization
   │
   ├─ ② Worker "lee-router" (worker.js)  strips /trackme, sets X-Forwarded-Prefix, proxies
   │
   └─→ ③ Cloud Run (track-me-…-uw.a.run.app, allow-unauthenticated)
          └─ viewer/auth.py re-validates the Access JWT
```

Access runs **before** the Worker, so unauthenticated users hit the login screen
and never reach your origin.

## Design: one app for everything (the model in use)

These are all *personal* apps gated to one person, so instead of a separate Access
application per path, a **single whole-host application** protects
`lee.vino9.net/*`:

- **One Access app + one policy** ("just me") covers every path.
- **One AUD tag**, shared by every backend's `CF_ACCESS_AUD`.
- Adding an app is a Worker `ROUTES` line + a deploy — **no Cloudflare dashboard
  change ever again**.
- Bonus: log in once, every app under `lee.vino9.net` is unlocked (SSO-ish).

Trade-off: an Access session for one path is valid at all of them — fine when
every app is gated to the same person. If you ever need an app reachable by a
*different* set of people, carve *that one* out into its own application (see
[Variant: per-path apps](#variant-per-path-apps-different-users-per-app)).

---

## Files

| File            | What it is                                                     |
| --------------- | ------------------------------------------------------------- |
| `worker.js`     | The path router. One `ROUTES` table maps `prefix → origin`.   |
| `wrangler.toml` | Deploy config; binds Worker `lee-router` to `lee.vino9.net/*`. |

---

## One-time setup (as built)

### 0. DNS

A **proxied** (orange-cloud) record for `lee` in the `vino9.net` zone. It never
serves traffic — the Worker route intercepts first — so a dummy target is fine:

```
Type A   Name lee   Content 192.0.2.1   Proxy: ON
```

### 1. The Worker

`ROUTES` in `worker.js` maps each context path to its Cloud Run origin (find a
service URL with `gcloud run services describe track-me --region us-west1
--format='value(status.url)'`). Deploy:

```bash
cd cf
wrangler deploy      # binds it to lee.vino9.net/* per wrangler.toml
```

Any path not in `ROUTES` returns 404.

### 2. The Access application

Zero Trust dashboard → **Access → Applications → Add an application →
Self-hosted → Public DNS**:

- **Destination**: subdomain `lee`, domain `vino9.net`, **path `*`** (whole host;
  an empty path is equivalent). App name: `lee`.
- **Policy**: Allow → Emails → `guru.lin@gmail.com`.
- After it's created: **Additional settings → AUD tag** → copy the
  **Application Audience (AUD) tag** (64-char hex).

### 3. The backend's JWT gate

`viewer/auth.py` re-validates the Access token on every request — this is what
actually protects the still-public `*.run.app` URL (Cloud Run is
`--allow-unauthenticated`, so the origin URL bypasses Cloudflare unless the app
checks the JWT itself). For track_me the two env vars are set **in CI**, in
`.github/workflows/python_build.yaml`'s `gcloud run deploy` step:

```
CF_ACCESS_TEAM_DOMAIN=vino9
CF_ACCESS_AUD=<the AUD tag from step 2>
```

- `CF_ACCESS_TEAM_DOMAIN` = the `<team>` in `<team>.cloudflareaccess.com`.
- `CF_ACCESS_AUD` **must be the real AUD**. Unset or set to the literal `ignore`
  sentinel, the gate **fails open** (allows) — the deploy-time off switch. Toggle
  Access off without a code change by setting it back to `ignore`.

---

## Adding another app

1. Deploy the app to its own Cloud Run service (make it subpath-aware — see below).
2. Add a line to `ROUTES` in `worker.js`, then `wrangler deploy`.
3. Set the **same** `CF_ACCESS_TEAM_DOMAIN` + `CF_ACCESS_AUD` on the new service.

No new DNS record, no new Worker, **no Cloudflare Access change** — the whole-host
`lee` app already covers the new path.

---

## Making an app subpath-aware (why the viewer code changed)

Behind a context path the browser sits at `lee.vino9.net/trackme/...`, so any
**root-absolute** URL an app emits (`href="/build"`, `fetch("/api/x")`) escapes
the app's mount point and 404s. The Worker sends `X-Forwarded-Prefix: /trackme`;
the app must turn every internal URL into `<prefix> + path`.

track_me does this:

- `viewer/app.py` wraps the WSGI app in Werkzeug's `ProxyFix(..., x_prefix=1)`,
  which moves `X-Forwarded-Prefix` into `SCRIPT_NAME` → exposed as
  `request.script_root`.
- The templates prefix internal links/fetches with `request.script_root` (server
  side) or a `const BASE = {{ request.script_root | tojson }}` (inline JS).

Locally `script_root` is empty, so `track-me serve` on `http://localhost:5000` is
unchanged. Any new backend needs the equivalent (most web frameworks honor
`X-Forwarded-Prefix` or a base-path/`SCRIPT_NAME` setting out of the box).

### Local smoke test (no Cloudflare needed)

Prove the prefix wiring end to end by faking the header the Worker would send:

```bash
# terminal 1
track-me serve

# terminal 2 — behaves as if mounted at /trackme
curl -s -H 'X-Forwarded-Prefix: /trackme' http://localhost:5000/ | grep -o 'href="[^"]*"'
# → hrefs come back as /trackme/build, /trackme/t/<id>, …
```

---

## Variant: per-path apps (different users per app)

If a future app needs a *different* audience (a teammate, a client), give **that
path** its own Access application instead of relying on the whole-host `lee` app:

1. Add a self-hosted **Public DNS** app with destination `lee.vino9.net` **path
   `<that-path>`** (scopes Access to `/<that-path>/*` only) and its own policy.
2. Copy *its* AUD tag and set that as the backend's `CF_ACCESS_AUD`.

That backend now validates only its own audience — a token minted for `lee`
won't pass, and vice versa. The whole-host `lee` app keeps covering everything
else. (Cloudflare evaluates the most specific path match first.)
