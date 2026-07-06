# Fronting apps under one hostname with Cloudflare (path routing + Zero Trust)

This directory holds the Cloudflare edge config that puts the track_me viewer —
and any other app you add later — behind **`lee.vino9.net/<path>/`**, gated by
**Cloudflare Access (Zero Trust)**, with **one** DNS record / cert / Worker for
the whole fleet.

```
Browser → lee.vino9.net/trackme/*
   │
   ├─ ① Cloudflare Access   challenges only /trackme/* → mints CF_Authorization
   │
   ├─ ② Worker (worker.js)  strips /trackme, sets X-Forwarded-Prefix, proxies
   │
   └─→ ③ Cloud Run (trackme-xxxx.run.app, allow-unauthenticated)
          └─ viewer/auth.py re-validates the Access JWT
```

Access runs **before** the Worker, so unauthenticated users hit the login screen
and never reach your origin. Each path is independent across all three layers, so
`/trackme` can be "just me" while `/notes` is a whole team.

---

## Files

| File            | What it is                                              |
| --------------- | ------------------------------------------------------- |
| `worker.js`     | The path router. One `ROUTES` table maps prefix→origin. |
| `wrangler.toml` | Deploy config; binds the Worker to `lee.vino9.net/*`.  |

---

## One-time setup

### 0. DNS

Create a **proxied** (orange-cloud) record for `apps` in the `vino9.net` zone. It
never actually serves traffic — the Worker route intercepts first — so a dummy
target is fine:

```
Type A   Name lee   Content 192.0.2.1   Proxy: ON
```

### 1. Deploy the Worker

Edit `worker.js` and set the real Cloud Run URL in `ROUTES` (find it with
`gcloud run services describe trackme --format='value(status.url)'`), then:

```bash
cd cf
wrangler deploy
```

The `routes` in `wrangler.toml` bind it to `lee.vino9.net/*`. Any path not in
`ROUTES` returns 404.

### 2. Create a Cloudflare Access application per path

Zero Trust dashboard → **Access → Applications → Add an application →
Self-hosted**:

- **Application domain**: subdomain `lee`, domain `vino9.net`, **path `trackme`**
  (this is what scopes Access to `/trackme/*` only).
- Add an **identity provider** and a **policy** (e.g. Allow → Emails →
  `guru.lin@gmail.com`).
- Save, then open the app and copy its **Application Audience (AUD) tag**.

Repeat per app you add later (each gets its own AUD + policy).

### 3. Point the backend's JWT gate at that AUD

`viewer/auth.py` re-validates the Access token on every request — this is what
actually protects the still-public `*.run.app` URL. Set on the Cloud Run service:

```bash
gcloud run services update trackme \
  --update-env-vars \
CF_ACCESS_TEAM_DOMAIN=<your-team>,\
CF_ACCESS_AUD=<the-AUD-tag-from-step-2>
```

- `CF_ACCESS_TEAM_DOMAIN` = the `<team>` in `<team>.cloudflareaccess.com`.
- `CF_ACCESS_AUD` **must be the real AUD**. Left unset or set to the literal
  `ignore` sentinel, the gate **fails open** and anyone with the run.app URL
  bypasses Access entirely.

---

## Adding another app

1. Deploy the app to its own Cloud Run service (make it subpath-aware — see below).
2. Add a line to `ROUTES` in `worker.js`, then `wrangler deploy`.
3. Create an Access application scoped to the new path (step 2 above).
4. Set that app's `CF_ACCESS_AUD` to the new AUD.

No new DNS record, no new Worker.

---

## Making an app subpath-aware (why the code changed)

Behind a context path the browser sits at `lee.vino9.net/trackme/...`, so any
**root-absolute** URL an app emits (`href="/build"`, `fetch("/api/x")`) escapes
the app's mount point and 404s. The Worker sends `X-Forwarded-Prefix: /trackme`;
the app must turn every internal URL into `<prefix> + path`.

track_me already does this:

- `viewer/app.py` wraps the WSGI app in Werkzeug's `ProxyFix(..., x_prefix=1)`,
  which moves `X-Forwarded-Prefix` into `SCRIPT_NAME` → exposed as
  `request.script_root`.
- The templates prefix internal links/fetches with `request.script_root` (server
  side) or a `const BASE = {{ request.script_root | tojson }}` (inline JS).

Locally `script_root` is empty, so `track-me serve` on `http://localhost:5000` is
unchanged. Any new backend needs the equivalent (most web frameworks honor
`X-Forwarded-Prefix` / a base-path setting out of the box).

---

## Local smoke test (no Cloudflare needed)

Prove the prefix wiring end to end by faking the header the Worker would send:

```bash
# terminal 1
track-me serve

# terminal 2 — behaves as if mounted at /trackme
curl -s -H 'X-Forwarded-Prefix: /trackme' http://localhost:5000/ | grep -o 'href="[^"]*"'
# → hrefs come back as /trackme/build, /trackme/t/<id>, …
```
