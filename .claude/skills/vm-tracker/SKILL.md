---
name: vm-tracker
description: |
  Manage the "tracker" GCE processing VM (Takeout/photo processing). "create":
  build it from the newest `tracker-base-<yyyymmdd>` snapshot and point the
  `tracker` ssh alias at it. "add scratch" / "delete scratch": attach or delete a
  throwaway /mnt/scratch disk (never snapshotted). "retire": snapshot the boot disk
  as a new `tracker-base-<yyyymmdd>`, then delete the VM.
  Creates and deletes billed cloud resources, so it is user-invoked only.
  Usage: "vm-tracker [create|add scratch [GB]|delete scratch|retire]"
disable-model-invocation: true
---

# vm-tracker

Scripts in `.claude/skills/vm-tracker/`. All talk to project `vino9-276317`, zone
`us-west1-b`, VM `tracker`, and print what they do.

| Script | What it does |
|---|---|
| `create_vm.sh [--machine-type T] [--snapshot S]` | Boot disk from the newest READY `tracker-base-*` snapshot, then the VM (default `e2-standard-2`, service account `worker-vm`, public IP), then `refresh_ssh`. No scratch disk. |
| `add_scratch.sh [GB]` | Starts the VM if stopped, creates a `<vm>-scratch` disk (default 150 GB), attaches it, formats it if blank, writes the fixed fstab line once, mounts `/mnt/scratch`. |
| `del_scratch.sh [--yes]` | Stops the VM, detaches and **deletes** the scratch disk. VM stays stopped. Without `--yes` it only prints the plan. |
| `retire_vm.sh [--yes]` | Stops the VM, snapshots the boot disk as `tracker-base-<yyyymmdd>` (UTC), waits until READY, then deletes the VM and all its disks. Without `--yes` it only prints the plan. |
| `lib.sh` | Shared helpers (constants, polling, `refresh_ssh`). Sourced, not run. |
| `update_ssh_config.py` | Edits `HostName` in `~/.ssh/config`, or adds a block cloned from `station`. Called by `refresh_ssh`. |

Boot snapshot = Debian 12, 20 GB, UEFI, `aidev` with sudo and its ssh key,
`/mnt/vinolab` (gcsfuse) and `~/.config/rclone/rclone.conf` baked in; to change
either, edit them on a VM and `retire` to make a new snapshot.

The public IP is ephemeral: it changes on every stop/start. `create_vm.sh` and
`add_scratch.sh` (when it starts a stopped VM) call `refresh_ssh`, which updates the
ssh alias, drops stale `known_hosts` entries and waits for ssh.

Because gcloud's long wait call is sometimes dropped by a proxy, every script runs
the command, tolerates an error, and then polls the real end state. A message like
"create reported an error; checking whether it went through..." is normal.

## Create

Run `create_vm.sh`. If the user asks for a different size, pass `--machine-type`
(`gcloud compute machine-types list --zones=us-west1-b`). It refuses if the VM
already exists; do not delete anything, tell the user. Then report the IP, check
`sudo -n true`, `mountpoint /mnt/vinolab` and `systemctl is-system-running`, and
say there is **no scratch disk yet** (`takeout-fetch` needs one). Remind them the
VM bills until stopped or retired.

## Scratch disk

`/mnt/scratch` holds fetched Takeout zips and extracted batches. When adding one,
ask how big (AskUserQuestion, **150 GB** first/default) unless they gave a size.

- `add_scratch.sh [GB]`. One scratch disk per VM.
- `del_scratch.sh` first **without** `--yes`; show the plan and say that
  **everything on the disk is lost and cannot be recovered**, and that the VM will
  be stopped (anything running on it, such as a `takeout-fetch`, is killed). Run
  with `--yes` only after an explicit go-ahead.
- To stop paying for scratch overnight: `del_scratch.sh --yes` already leaves the
  VM stopped. A stopped VM still bills for its boot disk, not for CPU.
- Resizing = delete then add.

## Retire

For when the VM will sit idle and you want to pay only for snapshot storage.
Run `retire_vm.sh` without `--yes`, show the plan, state that the VM and **every
disk including an attached scratch disk will be deleted** (only the boot disk is
snapshotted), and get an explicit go-ahead. It refuses if today's
`tracker-base-<yyyymmdd>` already exists. Running it on the VM being retired kills
your session; run it from your laptop.

Afterwards report the snapshot name and size
(`gcloud compute snapshots describe NAME --format="value(storageBytes)"`; the
first snapshot of a disk is full, later ones incremental, and the field can read 0
for a while). Older `tracker-base-*` snapshots still cost storage: list them, leave
deleting to the user.
