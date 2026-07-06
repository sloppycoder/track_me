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
| Python 3.12 + stdlib `sqlite3` | catalog + data layer (no ORM) |
| [Flask](https://flask.palletsprojects.com/) | the Google Maps timeline viewer |
| [H3](https://h3geo.org/) | spatial cells for batched geocoding + clustering |
| Google Maps API | reverse geocoding + the map viewer |
| [uv](https://docs.astral.sh/uv/) | dependency + virtualenv management |
| [ruff](https://docs.astral.sh/ruff/) / [ty](https://github.com/astral-sh/ty) | lint + format / type checking |

Requires **Python 3.12+**. Not Django; not a web app — a CLI plus a tiny local
viewer. All code lives under `src/track_me/`.

## Setup

```shell
uv sync                                   # create venv + install deps (+ the `track-me` CLI)
echo "GOOGLE_MAPS_API_KEY=..." > .env     # needed for the geocode step + the viewer
```

Local state (SQLite DB, thumbnails, timelines) lives under `userdata/` and is
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

## Build & view a travel timeline

```shell
# Build a timeline (preview; --write persists to userdata/timelines/<id>.json).
track-me timeline --start 2019-01-01 --end 2020-01-01 --level country
track-me timeline --start 2019-01-01 --end 2020-01-01 --level country \
    --write --id countries-2019 --title "Countries visited in 2019"

# Launch the Google Maps viewer at http://localhost:5000.
track-me serve
```

`--write` also embeds a compact per-photo **points** payload in the JSON so the
viewer can re-cluster on the fly; pass `--no-points` to omit it for a lighter file.

The viewer is interactive: a time-range slider (with a photo-density histogram)
under the map lets you scrub to any window, and the map POIs adapt from **country
→ city → neighborhood** as you narrow the range (or force a level with the
Auto / Country / City / Area toggle).

You can also build timelines from the browser: open `http://localhost:5000/build`,
drag the time-range slider, toggle Country/City, tune the merge/smoothing knobs
against a live map preview, then **Save**. It calls the same engine as
`track-me timeline`, so the saved JSON is identical to the CLI for the same knobs.

## Quality

```shell
ruff format <file.py>
ruff check . --fix
ruff check .
pytest
ty check .
```

## Deploy (Cloud Run)

The viewer ships to **Cloud Run** (region `us-west1`) from GitHub Actions: every
push lints + tests, and a push to `main` builds the container and deploys it. The
concrete project / bucket / service-account values live only in the workflow's
`env:` block; the docs use placeholders. Runtime data (the SQLite DB + timelines)
is **not** in the image — a GCS bucket is mounted at `/mnt/gcs` and the app reads
it via `TRACKME_USERDATA=/mnt/gcs/track_me`. Auth is keyless via **Workload
Identity Federation** (no service-account keys in GitHub).

Public access is gated by **Cloudflare Zero Trust Access** at
`lee.vino9.net/trackme/` — a single Cloudflare Worker path-routes the hostname to
the Cloud Run backend and the viewer validates the Access JWT. See
**[`cf/README.md`](cf/README.md)**.

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
BUCKET=your-data-bucket                         # GCS bucket for the DB + timelines
SA=track-me@${PROJECT_ID}.iam.gserviceaccount.com

# APIs + the image registry
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  iamcredentials.googleapis.com sts.googleapis.com \
  secretmanager.googleapis.com storage.googleapis.com --project="$PROJECT_ID"
gcloud artifacts repositories create track-me --project="$PROJECT_ID" \
  --repository-format=docker --location=us-west1

# The service account
gcloud iam service-accounts create track-me --project="$PROJECT_ID" \
  --display-name="track_me viewer (Cloud Run + GitHub deploy)"

# Workload Identity Federation, locked to one GitHub repo
gcloud iam workload-identity-pools create github-pool \
  --project="$PROJECT_ID" --location=global --display-name="GitHub Actions pool"
gcloud iam workload-identity-pools providers create-oidc github-provider \
  --project="$PROJECT_ID" --location=global --workload-identity-pool=github-pool \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='${REPO}'"

# Roles: (1) let the repo impersonate the SA, (2) let the SA run-as itself,
# (3) deploy rights, (4) read/write the mounted bucket.
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
`REPO` above, so they're safe even in a public repo. The Maps-key secret and
seeding the bucket are covered in **`DEPLOY.md`**.

## More

- **`DEPLOY.md`** — Cloud Run deployment runbook.
- **`cf/README.md`** — Cloudflare edge: path routing (Worker) + Zero Trust Access.
- **`CLAUDE.md`** — working agreement for coding agents (structure, commands).
