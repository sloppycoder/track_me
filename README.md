# track_me

A local-first tool that turns **Google Takeout** photo exports into a clean,
queryable travel timeline, then visualizes it on **Google Maps**. It parses each
photo's Takeout sidecar JSON (plus EXIF) for an authoritative timestamp and
location, reverse-geocodes coordinates into place names, and keeps a deep link
back to the original on Google Photos. The Takeout extract is treated as
transient — everything is re-derivable, so the local SQLite catalog is the source
of truth you keep.

## Stack

| Tool | Purpose |
| --- | --- |
| Python 3.14 + stdlib `sqlite3` | catalog + data layer (no ORM) |
| [FastMCP](https://gofastmcp.com/) | MCP server for chat clients (ChatGPT, Claude) |
| [H3](https://h3geo.org/) | spatial cells for batched geocoding + clustering |
| Google Maps API | reverse geocoding |
| [uv](https://docs.astral.sh/uv/) | dependency + virtualenv management |
| [ruff](https://docs.astral.sh/ruff/) / [ty](https://github.com/astral-sh/ty) | lint + format / type checking |

Requires **Python 3.14+**. Not Django; not a web app — a CLI plus an MCP server. All code lives under `src/track_me/`.

## Setup

```shell
uv sync                                   # create venv + install deps (+ the `track-me` CLI)
echo "GOOGLE_MAPS_API_KEY=..." > .env     # needed for the geocode step
```

Local state (SQLite DB, thumbnails) lives under `userdata/` and is
gitignored. Point it elsewhere with `TRACKME_USERDATA` (default `./userdata`).
The schema is created automatically on first use — no migrations.

## The pipeline

```shell
# 1. INGEST a Takeout source (local dir or s3://bucket/prefix): match sidecars,
#    set taken_at + local_date + timezone, resolve location, store the Photos link.
#    Parallel + sidecar-first (reads image bytes only when the sidecar lacks data).
track-me ingest <source> [--thumbnails] [--force] \
                 [--filter YYYY-MM[,YYYY-MM]] [--workers 32]

# 2. GEOCODE located items into place names (H3-batched Google calls). Fetch stores
#    the raw response; derive picks city/admin1 offline (re-runnable, free).
track-me geocode [--resolution 9] [--max-api-calls N] [--recalculate]
track-me geocode --estimate       # count API calls / cost without calling
track-me geocode --derive-only    # recompute city/admin1 from stored responses

# 3. EXPORT located media as a timestamped track for other timeline tools.
track-me export [--format gpx|geojson] [--year YYYY] [--output FILE]
```

`ingest` and `geocode` are **re-runnable and incremental**: already-seen items
(matched by `dedupe_key`) are skipped and manual edits are never overwritten.

## Preview a travel timeline

```shell
track-me timeline --start 2019-01-01 --end 2020-01-01 --level country
```

Prints the chronological, non-overlapping stays for the window (`--level city` for
an itinerary). The same engine backs the MCP server's `get_trips` tool.

## Ask questions from a chat client (MCP server)

`track-me mcp` serves the catalog to MCP chat clients (ChatGPT, Claude, Gemini
CLI …) as **read-only tools** — you ask in plain language, the model calls them:

| Tool | Answers |
| --- | --- |
| `catalog_overview` | date span, totals, photos per country |
| `find_places` | exact stored spelling of a city/region ("Hà Nội") |
| `find_photos` | photos by date / country / city / near a point (sampled, with links) |
| `get_trips` | the travel timeline: country- or city-level stays |
| `place_stats` | rank countries or cities by photos / days |
| `busiest_days` | the days with the most photos in a window |

Results carry each photo's Google Photos link (opens the app on Android) and a
Google Maps link. No images are returned.

```shell
track-me mcp --stdio              # local MCP client over stdio, no sign-in
track-me mcp --no-auth            # local test: http://127.0.0.1:8080/mcp, no sign-in
track-me mcp                      # HTTP + Google OAuth (what Cloud Run runs)
```

The catalog is read from `./userdata/track_me.db` relative to the working
directory (override with `TRACKME_USERDATA` or `DB_PATH`). The server opens it
**read-only and immutable** — it takes no SQLite locks, so `ingest`/`geocode` can
run against the same file while it's up.

### Local use over stdio (no auth)

A desktop MCP client (Claude Desktop, Claude Code, Gemini CLI, Cursor …) can launch
the server itself. The client starts it as a subprocess on your machine, so there's
no network listener and no sign-in. `uv --directory` makes the repo the working
directory, so the default `./userdata` resolves:

```json
{
  "mcpServers": {
    "track_me": {
      "command": "uv",
      "args": ["--directory", "/path/to/track_me", "run", "track-me", "mcp", "--stdio"]
    }
  }
}
```

With Claude Code: `claude mcp add track_me -- uv --directory /path/to/track_me run
track-me mcp --stdio`.

### Remote use over HTTP (Google OAuth)

Over HTTP (without `--no-auth`) the server requires **Google sign-in** and only
admits the verified emails in `MCP_ALLOWED_EMAILS`. It needs `MCP_BASE_URL`,
`GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` and `MCP_ALLOWED_EMAILS`
(and `TRACKME_DB_URI=gs://bucket/key` to copy the catalog from GCS at startup) — see **`DEPLOY.md`**.
To connect ChatGPT, add a custom connector / app with the URL
`https://<service>.run.app/mcp` and OAuth authentication; it walks you through
the Google sign-in.

## Quality

```shell
ruff format <file.py>
ruff check . --fix
ruff check .
pytest
ty check .
```

## Deploy (Cloud Run)

The **MCP server** ships to **Cloud Run** (region `us-west1`) from GitHub Actions: every
push lints + tests, and a push to `main` builds the container and deploys it. The
concrete project / bucket / service-account values live only in the workflow's
`env:` block; the docs use placeholders. Runtime data (the SQLite DB)
is **not** in the image — the app copies it from GCS to `/tmp` at cold start
(`TRACKME_DB_URI`). Deploy auth is keyless via **Workload
Identity Federation** (no service-account keys in GitHub). Users authenticate to
the server with **Google OAuth**, limited to an email allowlist.

**`DEPLOY.md`** has the full runbook (what's already provisioned, the remaining
one-time steps, and how to run the image locally). The identity setup below is
reproduced here so you can (re)create it by hand.

### One-time identity setup (service account + federation)

A single dedicated service account is both the GitHub **deployer** (impersonated
via WIF) and the Cloud Run **runtime** identity. Run once per project:

```shell
PROJECT_ID=your-gcp-project                     # e.g. the GCP project id
PROJECT_NUM=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
REPO=your-org/your-repo                         # owner/repo allowed to deploy
BUCKET=your-data-bucket                         # GCS bucket for the DB
SA=track-me@${PROJECT_ID}.iam.gserviceaccount.com

# APIs + the image registry
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  iamcredentials.googleapis.com sts.googleapis.com \
  secretmanager.googleapis.com storage.googleapis.com --project="$PROJECT_ID"
gcloud artifacts repositories create track-me --project="$PROJECT_ID" \
  --repository-format=docker --location=us-west1

# The service account
gcloud iam service-accounts create track-me --project="$PROJECT_ID" \
  --display-name="track_me MCP server (Cloud Run + GitHub deploy)"

# Workload Identity Federation, locked to one GitHub repo
gcloud iam workload-identity-pools create github-pool \
  --project="$PROJECT_ID" --location=global --display-name="GitHub Actions pool"
gcloud iam workload-identity-pools providers create-oidc github-provider \
  --project="$PROJECT_ID" --location=global --workload-identity-pool=github-pool \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='${REPO}'"

# Roles: (1) let the repo impersonate the SA, (2) let the SA run-as itself,
# (3) deploy rights, (4) read the DB from the bucket.
PRINCIPAL="principalSet://iam.googleapis.com/projects/${PROJECT_NUM}/locations/global/workloadIdentityPools/github-pool/attribute.repository/${REPO}"
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --role=roles/iam.workloadIdentityUser --member="$PRINCIPAL"
gcloud iam service-accounts add-iam-policy-binding "$SA" \
  --role=roles/iam.serviceAccountUser --member="serviceAccount:${SA}"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA}" --role=roles/run.admin --condition=None
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA}" --role=roles/artifactregistry.writer --condition=None
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${SA}" --role=roles/storage.objectUser
```

Then set two repo secrets (**Settings → Secrets and variables → Actions**):

| Secret | Value |
| --- | --- |
| `GCP_WIF_PROVIDER` | `projects/<PROJECT_NUM>/locations/global/workloadIdentityPools/github-pool/providers/github-provider` |
| `GCP_SERVICE_ACCOUNT` | `track-me@<PROJECT_ID>.iam.gserviceaccount.com` |

Neither value is a credential — the WIF provider only mints tokens for the exact
`REPO` above, so they're safe even in a public repo. The OAuth client + secrets
and seeding the bucket are covered in **`DEPLOY.md`**.

## More

- **`DEPLOY.md`** — Cloud Run deployment runbook.
- **`AGENTS.md`** — working agreement for coding agents (structure, commands).
