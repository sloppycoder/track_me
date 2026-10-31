#!/usr/bin/env bash
# GCE startup script (runs as root on every boot; idempotent).
# Installs the tooling for Takeout/photo processing, then drops a marker file.
set -euo pipefail

MARKER=/var/lib/track-me-vm-ready
[ -f "$MARKER" ] && exit 0

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl git tmux unzip jq htop gnupg build-essential

# GitHub CLI (official apt repo)
install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
  -o /etc/apt/keyrings/githubcli-archive-keyring.gpg
chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
  >/etc/apt/sources.list.d/github-cli.list
apt-get update
apt-get install -y gh

# rclone: Debian's package lags badly; use the upstream installer
curl -fsSL https://rclone.org/install.sh | bash

# uv: system-wide so every ssh user gets it
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh

# Node.js + Codex CLI (system-wide)
apt-get install -y nodejs npm
npm install -g @openai/codex

touch "$MARKER"
