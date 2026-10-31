# Deploying the MCP server to Cloud Run

The GitHub Action (`.github/workflows/python_build.yaml`) lints + tests every
push, then on a push to `main` (or a manual **Run workflow**) it builds the
**MCP server** container (`track-me mcp`) and deploys it to **Cloud Run**, region
**`us-west1`** (Oregon), as the `track-me` service. The concrete project / bucket /
service-account values live only in that workflow's `env:` block — this doc uses
placeholders.

The service exposes read-only catalog tools at **`<SERVICE_URL>/mcp`** for chat
clients (ChatGPT, Claude …). Access is **Google OAuth** handled by the app itself
(FastMCP's OAuth proxy) and limited to the emails in `MCP_ALLOWED_EMAILS`; Cloud
Run runs `--allow-unauthenticated` because chat clients can't mint Cloud Run IAM
tokens.

Runtime state (the SQLite DB) is **not** baked into the image — at startup the app
copies `gs://<DATA_BUCKET>/track_me/track_me.db` to `/tmp` (set via
`TRACKME_DB_URI`; the service account needs read access to the bucket) and serves
from the copy. `/tmp` is in-memory on Cloud Run, so the container has 1 GiB. The
bucket is still mounted at `/mnt/gcs`, but only for OAuth state: `FASTMCP_HOME`
(client registrations + encrypted tokens) lives there so scale-to-zero doesn't sign
chat clients out. Queries never touch the mount.

Deploy auth is keyless via **Workload Identity Federation** — no service-account
keys in GitHub.

Placeholders used below (substitute your own):

| Placeholder | Meaning |
| --- | --- |
| `<PROJECT_ID>` | GCP project id |
| `<PROJECT_NUM>` | GCP project number (`gcloud projects describe <PROJECT_ID> --format='value(projectNumber)'`) |
| `<DATA_BUCKET>` | GCS bucket holding the runtime DB + OAuth state |
| `<OWNER>/<REPO>` | the GitHub repo allowed to deploy |
| `<SERVICE_URL>` | the service's public URL, e.g. `https://track-me-<PROJECT_NUM>.us-west1.run.app` |

## Identity

One dedicated service account, `track-me@<PROJECT_ID>.iam.gserviceaccount.com`,
does double duty:

- **as the GitHub deployer** (impersonated via WIF): `roles/run.admin`,
  `roles/artifactregistry.writer`, and `serviceAccountUser` on itself.
- **as the Cloud Run runtime**: `roles/storage.objectUser` on the data bucket
  (read the DB, read/write OAuth state) and `roles/secretmanager.secretAccessor`
  on the OAuth secrets.

Creating the SA + federation is a one-time step — see **`README.md` → Deploy
(Cloud Run) → One-time identity setup**, the single source of truth for it. That
setup also enables the required APIs and creates the Artifact Registry docker
repo `track-me` in `us-west1`.

## Remaining one-time steps

### 1. Find the service URL

The server must know its public URL: it is advertised in the OAuth metadata and
the Google redirect URI is `<SERVICE_URL>/auth/callback`. The workflow resolves
it on every deploy:

- the repo **variable** `MCP_BASE_URL` if set (**Settings → Secrets and
  variables → Actions → Variables**) — use this for a custom domain, or
- otherwise the existing `track-me` service's URL (`gcloud run services describe
  track-me --region us-west1 --format='value(status.url)'`), which stays the same
  across deploys. On the very first deploy no service exists yet, so set
  `MCP_BASE_URL` to `https://track-me-<PROJECT_NUM>.us-west1.run.app`.

The deploy job prints both the MCP endpoint and the redirect URI as a notice.

### 2. Create the Google OAuth client

In the GCP console (**Google Auth Platform**, formerly *APIs & Services →
OAuth consent screen / Credentials*):

1. **Branding / audience:** an *External* app is fine. While it's in *Testing*,
   add your own Google account as a **test user** — only test users can sign in,
   which is an extra lock on top of the allowlist. Scopes needed: `openid` and
   `.../auth/userinfo.email` (non-sensitive, no verification required).
2. **Clients → Create client → Web application**, with the authorized redirect URI
   **`<SERVICE_URL>/auth/callback`** (exactly, no trailing slash).
3. Note the client ID and client secret.

### 3. Create the secrets + grant the SA access

```bash
PROJECT_ID=<PROJECT_ID>
SA=track-me@${PROJECT_ID}.iam.gserviceaccount.com

printf '%s' 'CLIENT_ID.apps.googleusercontent.com' | \
  gcloud secrets create my-mcp-oauth-client-id --project="$PROJECT_ID" --data-file=-
printf '%s' 'GOCSPX-...' | \
  gcloud secrets create my-mcp-oauth-client-secret --project="$PROJECT_ID" --data-file=-
# comma-separated Google account emails allowed to use the server
printf '%s' 'you@gmail.com' | \
  gcloud secrets create track-me-mcp-allowed-emails --project="$PROJECT_ID" --data-file=-
# rotate later: gcloud secrets versions add <name> --data-file=-

for s in my-mcp-oauth-client-id my-mcp-oauth-client-secret track-me-mcp-allowed-emails; do
  gcloud secrets add-iam-policy-binding "$s" --project="$PROJECT_ID" \
    --member="serviceAccount:${SA}" --role="roles/secretmanager.secretAccessor"
done
```

The client ID and the allowlist aren't credentials, but keeping them in Secret
Manager keeps them (and your email) out of the repo. The tokens the server hands
to clients are signed with a key derived from the client secret, so **rotating
the client secret signs every client out** (they just sign in again).

### 4. Seed the bucket with the catalog

Expected layout:

```
gs://<DATA_BUCKET>/track_me/track_me.db
gs://<DATA_BUCKET>/track_me/mcp_oauth/     # created by the server (OAuth state)
```

From a local `userdata/`:

```bash
gsutil cp userdata/track_me.db gs://<DATA_BUCKET>/track_me/track_me.db
```

The server opens its local copy read-only + immutable (no locks). After uploading
a new copy, deploy a new revision (or restart the service) so instances re-copy it.

### 5. Add the two GitHub repo secrets

**Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
| --- | --- |
| `GCP_WIF_PROVIDER` | `projects/<PROJECT_NUM>/locations/global/workloadIdentityPools/github-pool/providers/github-provider` |
| `GCP_SERVICE_ACCOUNT` | `track-me@<PROJECT_ID>.iam.gserviceaccount.com` |

Neither value is a credential — the WIF provider only mints tokens for the exact
`<OWNER>/<REPO>` it was locked to — but keeping them as secrets keeps the concrete
ids out of Actions logs.

---

## Deploy

Push to `main`, or trigger **Actions → python_build → Run workflow**. The `deploy`
job builds the image, pushes it to Artifact Registry, resolves the service URL,
and runs `gcloud run deploy`.

## Connect ChatGPT

ChatGPT supports custom MCP servers only on **web** and only on some plans, and **not
on mobile** (see README, "Which clients work"). Where available, add a custom connector / app
with the server URL **`<SERVICE_URL>/mcp`** and **OAuth** authentication. ChatGPT
registers itself with the server, sends you to a consent page and then to Google
sign-in; sign in with an allowlisted account. Any other Google account completes
sign-in but every request is rejected with 401. Claude (custom connector) works
the same way.

Quick check that the gate is up — an unauthenticated call must be refused:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST <SERVICE_URL>/mcp   # expect 401
curl -s <SERVICE_URL>/.well-known/oauth-authorization-server         # OAuth metadata
```

## Run locally

Without the container, no sign-in (loopback only):

```bash
uv run track-me mcp --no-auth        # http://127.0.0.1:8080/mcp
```

The container always runs with OAuth, so it needs the same settings as Cloud Run
(add `http://localhost:8080/auth/callback` as a redirect URI on the OAuth client):

```bash
docker build -t track-me .
docker run --rm -p 8080:8080 \
  -e TRACKME_USERDATA=/data -e FASTMCP_HOME=/data/mcp_oauth \
  -e MCP_BASE_URL=http://localhost:8080 \
  -e GOOGLE_OAUTH_CLIENT_ID=... -e GOOGLE_OAUTH_CLIENT_SECRET=... \
  -e MCP_ALLOWED_EMAILS=you@gmail.com \
  -v "$PWD/userdata:/data" \
  track-me
# MCP endpoint: http://localhost:8080/mcp
```
