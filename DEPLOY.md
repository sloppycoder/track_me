# Deploying the viewer to Cloud Run

The GitHub Action (`.github/workflows/python_build.yaml`) lints + tests every
push, then on a push to `main` (or a manual **Run workflow**) it builds the
viewer container and deploys it to **Cloud Run**, region **`us-west1`** (Oregon).
The concrete project / bucket / service-account values live only in that
workflow's `env:` block — this doc uses placeholders.

Runtime state (the SQLite DB + `timelines/*.json`) is **not** baked into the
image — a GCS bucket is mounted at `/mnt/gcs` and the app is pointed at it via
`TRACKME_USERDATA=/mnt/gcs/track_me`, so `config.py` reads the DB and timelines
straight from GCS.

Auth is keyless via **Workload Identity Federation** — no service-account keys in
GitHub.

Placeholders used below (substitute your own):

| Placeholder | Meaning |
| --- | --- |
| `<PROJECT_ID>` | GCP project id |
| `<PROJECT_NUM>` | GCP project number (`gcloud projects describe <PROJECT_ID> --format='value(projectNumber)'`) |
| `<DATA_BUCKET>` | GCS bucket holding the runtime DB + timelines |
| `<OWNER>/<REPO>` | the GitHub repo allowed to deploy |

## Identity

One dedicated service account, `track-me@<PROJECT_ID>.iam.gserviceaccount.com`,
does double duty:

- **as the GitHub deployer** (impersonated via WIF): `roles/run.admin`,
  `roles/artifactregistry.writer`, and `serviceAccountUser` on itself.
- **as the Cloud Run runtime**: `roles/storage.objectUser` on the data bucket
  (read/write timelines + DB) and `roles/secretmanager.secretAccessor` on the
  Maps-key secret.

Creating the SA + federation is a one-time step — see **`README.md` → Deploy
(Cloud Run) → One-time identity setup**, the single source of truth for it. That
setup also enables the required APIs and creates the Artifact Registry docker
repo `track-me` in `us-west1`.

## Remaining one-time steps

### 1. Create the Maps-key secret + grant the SA access

```bash
PROJECT_ID=<PROJECT_ID>
SA=track-me@${PROJECT_ID}.iam.gserviceaccount.com

printf '%s' 'YOUR_MAPS_API_KEY' | \
  gcloud secrets create track-me-maps-key --project="$PROJECT_ID" --data-file=-
# rotate later: gcloud secrets versions add track-me-maps-key --data-file=-

gcloud secrets add-iam-policy-binding track-me-maps-key --project="$PROJECT_ID" \
  --member="serviceAccount:${SA}" --role="roles/secretmanager.secretAccessor"
```

> **Restrict the Maps key itself.** The viewer injects it into the map page, so
> it is visible in the browser by design — Secret Manager does not hide it from
> end users. In the GCP console (APIs & Services → Credentials), lock the key
> down so a leak is useless off your site:
> - **Application restriction:** HTTP referrers → your Cloud Run URL,
>   e.g. `https://track-me-*.run.app/*`.
> - **API restriction:** allow only the **Maps JavaScript API**.

### 2. Seed the bucket with runtime data

Expected layout:

```
gs://<DATA_BUCKET>/track_me/track_me.db
gs://<DATA_BUCKET>/track_me/timelines/<id>.json
```

From a local `userdata/` (built with `track-me timeline --write`):

```bash
gsutil -m cp    userdata/track_me.db   gs://<DATA_BUCKET>/track_me/track_me.db
gsutil -m cp -r userdata/timelines     gs://<DATA_BUCKET>/track_me/timelines
```

### 3. Add the two GitHub repo secrets

**Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
| --- | --- |
| `GCP_WIF_PROVIDER` | `projects/<PROJECT_NUM>/locations/global/workloadIdentityPools/github-pool/providers/github-provider` |
| `GCP_SERVICE_ACCOUNT` | `track-me@<PROJECT_ID>.iam.gserviceaccount.com` |

Neither value is a credential — the WIF provider only mints tokens for the exact
`<OWNER>/<REPO>` it was locked to — but keeping them as secrets keeps the concrete
ids out of Actions logs.

### 4. (Optional) Front with Cloudflare Access

Cloud Run runs `--allow-unauthenticated`; the app itself is gated by Cloudflare
**Zero Trust Access** at `lee.vino9.net/trackme/`, routed by a Cloudflare Worker.
The deploy step sets `CF_ACCESS_TEAM_DOMAIN` + `CF_ACCESS_AUD` so `viewer/auth.py`
validates the Access JWT (and closes the direct `*.run.app` bypass); `CF_ACCESS_AUD=ignore`
turns it off. Full edge setup (Worker, DNS, the Access app, subpath-awareness) is
documented in **[`cf/README.md`](cf/README.md)**.

---

## Deploy

Push to `main`, or trigger **Actions → python_build → Run workflow**. The `deploy`
job builds the image, pushes it to Artifact Registry, and runs `gcloud run
deploy`. The service URL is printed at the end of the job (and shown in the Cloud
Run console).

## Build / run the container locally

```bash
docker build -t track-me .
docker run --rm -p 8080:8080 \
  -e GOOGLE_MAPS_API_KEY=YOUR_KEY \
  -e TRACKME_USERDATA=/data \
  -v "$PWD/userdata:/data" \
  track-me
# open http://localhost:8080
```
