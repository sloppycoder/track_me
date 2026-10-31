---
name: provision-vm
description: Create (or reuse) the GCP VM used to process Google Takeout photo exports for track_me, install its tooling, and wire up GitHub + rclone/Drive access from Secret Manager. Run this LOCALLY (needs gcloud). Use when the user asks to provision/create/set up the processing VM, or when the takeout-catalog skill needs a machine.
---

# Provision the Takeout processing VM

Runs **on the user's laptop** with `gcloud`. It creates the VM, then configures it
over `gcloud compute ssh`. The catalog work itself happens on the VM later (see the
`takeout-catalog` skill).

## Fixed parameters

| Setting | Value |
|---|---|
| Project | `vino9-276317` |
| Zone | `us-west1-b` (Oregon; try `-a`/`-c` if out of capacity) |
| Name | `track-me-proc` |
| Machine | `e2-standard-2` (2 vCPU / 8 GB) |
| Image | `--image-family=debian-13 --image-project=debian-cloud` (Debian 13 "trixie"; fall back to `debian-12`) |
| Disk | 200 GB `pd-balanced`. Sized from `rclone lsf gdrive:Takeout --format sp`: the current Takeout is ~113 GB across 57 zips (largest batch ~100 GB, largest zip 2.15 GB). Re-check with that command if the Takeout has grown a lot and bump `--boot-disk-size`. |
| Service account | `worker-vm@vino9-276317.iam.gserviceaccount.com` (dedicated; created by step 2 if missing) with `roles/secretmanager.secretAccessor` and `roles/aiplatform.user` |
| Secrets read | `gh-pat` (GitHub token), `rclone-gdrive-conf` (rclone.conf) |
| Repo | `https://github.com/sloppycoder/track_me` → `~/git/track_me` |

## Steps

1. **Reuse if it exists.** `gcloud compute instances describe track-me-proc --zone us-west1-b --project vino9-276317`.
   If present and `RUNNING`, skip to step 4. If `TERMINATED`, `gcloud compute instances start` it.
2. **Ensure the service account** (idempotent; skip creation if it exists):
   ```bash
   SA=worker-vm@vino9-276317.iam.gserviceaccount.com
   gcloud iam service-accounts describe $SA --project=vino9-276317 >/dev/null 2>&1 || \
     gcloud iam service-accounts create worker-vm --project=vino9-276317 \
       --display-name="Worker VM (Takeout processing)"
   for role in roles/secretmanager.secretAccessor roles/aiplatform.user; do
     gcloud projects add-iam-policy-binding vino9-276317 \
       --member=serviceAccount:$SA --role=$role --condition=None --quiet >/dev/null
   done
   ```
   Roles are project-level (the `gh-pat` / `rclone-gdrive-conf` secrets may not
   exist yet, so they can't be bound individually). Tighten to per-secret bindings
   later if wanted. Vertex AI also needs the `aiplatform.googleapis.com` API
   enabled (`gcloud services enable aiplatform.googleapis.com`).
3. **Create the VM:**
   ```bash
   gcloud compute instances create track-me-proc \
     --project=vino9-276317 --zone=us-west1-b \
     --machine-type=e2-standard-2 \
     --image-family=debian-13 --image-project=debian-cloud \
     --boot-disk-size=200GB --boot-disk-type=pd-balanced \
     --service-account=worker-vm@vino9-276317.iam.gserviceaccount.com \
     --scopes=cloud-platform \
     --metadata-from-file=startup-script=.claude/skills/provision-vm/startup.sh
   ```
   `startup.sh` (same dir as this file) installs git, curl, tmux, unzip, jq, gh, rclone
   (upstream installer), uv, Node.js and the Codex CLI (`@openai/codex`, system-wide), then writes `/var/lib/track-me-vm-ready`.
4. **Wait for SSH and the startup script** (poll up to ~5 min):
   ```bash
   gcloud compute ssh track-me-proc --zone us-west1-b --project vino9-276317 \
     --command 'test -f /var/lib/track-me-vm-ready && echo ready'
   ```
   If SSH is refused, retry with `--tunnel-through-iap`. The `default` network already
   allows tcp:22.
5. **Check the secrets exist before configuring** (locally):
   `gcloud secrets list --project vino9-276317 --format='value(name)'`. If `gh-pat` or
   `rclone-gdrive-conf` is missing, stop and tell the user which one to create (as of
   2026-10-31 neither existed). Do not invent values.
6. **Configure the VM in one ssh call** (secrets are read on the VM with its own
   service account; never echo secret values):
   ```bash
   gcloud compute ssh track-me-proc --zone us-west1-b --project vino9-276317 --command '
   set -euo pipefail
   P=vino9-276317
   # GitHub login with the PAT
   gcloud secrets versions access latest --secret=gh-pat --project=$P | gh auth login --with-token
   gh auth setup-git
   gh auth status
   # rclone config
   mkdir -p ~/.config/rclone
   # the secret value is base64-encoded; decode it
   gcloud secrets versions access latest --secret=rclone-gdrive-conf --project=$P | base64 -d > ~/.config/rclone/rclone.conf
   chmod 600 ~/.config/rclone/rclone.conf
   # Claude Code (native per-user installer -> ~/.local/bin)
   command -v claude >/dev/null || curl -fsSL https://claude.ai/install.sh | bash
   grep -q ".local/bin" ~/.profile || echo "export PATH=\$HOME/.local/bin:\$PATH" >> ~/.profile
   export PATH=$HOME/.local/bin:$PATH
   claude --version; codex --version
   # clone + sync
   cd $HOME && mkdir -p git && cd git
   [ -d track_me ] || gh repo clone sloppycoder/track_me
   cd track_me && uv sync
   # rclone smoke test
   rclone lsf gdrive:Takeout | head
   '
   ```
   Idempotent: re-running re-logs-in, refreshes rclone.conf, skips the clone if present.
7. **Report:** VM name/zone/external IP, `gh auth status` user, whether `uv sync`
   succeeded, the `claude` and `codex` versions (both need a one-time interactive login on the VM), and the first lines of `rclone lsf gdrive:Takeout`. If the rclone test
   fails (auth error, token expired), say so plainly: the fix is a new
   `rclone-gdrive-conf` secret version, not a retry.

## Notes

- Cost: e2-standard-2 + 200 GB pd-balanced ≈ $70/mo running. Offer
  `gcloud compute instances stop track-me-proc ...` when the user is done; it keeps
  the disk (and clone/scratch) for the next run. Only delete the VM if asked.
- `rclone` warns that the shared Drive client_id is being retired during 2026; if
  listings start failing with quota/auth errors, the rclone config needs its own
  client_id.
