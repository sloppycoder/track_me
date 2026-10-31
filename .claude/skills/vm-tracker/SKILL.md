---
name: vm-tracker
description: |
  Manage the "tracker" GCE processing VM (Takeout/photo processing) across idle
  periods. "create": build it from the newest `tracker-base-<yyyymmdd>` boot
  snapshot (boot disk only), then point `tracker` in ~/.ssh/config at the new
  public IP. "scratch add/remove/status": hot-add or delete a throwaway
  /mnt/scratch disk on the running VM (never snapshotted). "stop": remove the
  scratch disk, then stop the VM. "retire": snapshot the boot disk as a new
  `tracker-base-<yyyymmdd>`, then delete the VM and its disks.
  Creates and deletes billed cloud resources, so it is user-invoked only.
  Usage: "vm-tracker [create|scratch|stop|retire] [options in words]"
  Examples: "vm-tracker", "vm-tracker create 4 cpus 16GB",
  "vm-tracker scratch add 200", "vm-tracker scratch remove", "vm-tracker stop",
  "vm-tracker retire"
disable-model-invocation: true
---

# vm-tracker

Scripts (in `.claude/skills/vm-tracker/`; `-h` lists all flags):

- `create_vm.sh`: provisions the boot disk and instance (no scratch disk).
  Last stdout line on success: `RESULT <vm-name> <status> <ip>`.
- `scratch_disk.sh add [GB] | remove [--yes] | status`: manages the scratch disk
  of the running VM. Last line: `RESULT <vm-name> <disk> added|removed|present|none`.
- `stop_vm.sh [--yes]`: `scratch_disk.sh remove --yes`, then stops the VM.
- `retire_vm.sh`: snapshots the boot disk, then deletes the VM. Last line:
  `RESULT <vm-name> deleted|kept <snapshot>`.
- `update_ssh_config.py`: rewrites/adds the `HostName` of one `Host` block.

Defaults: project `vino9-276317`, zone `us-west1-b`, VM name `tracker`,
machine `e2-standard-2` (2 vCPU / 8 GB), service account
`worker-vm@vino9-276317.iam.gserviceaccount.com`, public IP (ephemeral).
The boot snapshot is the newest `tracker-base-<yyyymmdd>` (Debian 12, 20 GB,
UEFI, `aidev` user with sudo and its ssh key baked in).

**Scratch is separate from the VM.** `create` makes no scratch disk. A scratch
disk is a blank `pd-balanced` disk `<vm>-scratch`, hot-attached with device name
`scratch`, formatted ext4 and mounted at `/mnt/scratch` by `scratch_disk.sh add`,
and **deleted** by `remove`. It is never snapshotted, so it costs money only while
it exists. Stopped VMs still bill for attached disks: remove scratch before
stopping (`stop` does both).

The action is `create` unless the user says scratch / stop / retire / shut down
for a while / snapshot and delete / park.

## Create

### 2. Translate the rest of the request into flags

| User says | Flags |
|---|---|
| a VM name | positional `VM_NAME` (default `tracker`) |
| `e2-standard-4`, … | `--machine-type TYPE` |
| "4 cpus 16GB" | `--cpus 4 --memory 16` (family `e2` unless they name one: `--family n2d`) |
| a specific snapshot | `--snapshot NAME` (default: newest `tracker-base-*`) |
| boot disk bigger | `--boot-size GB` (must be ≥ snapshot size, 20) |

List snapshots with:
`gcloud compute snapshots list --project=vino9-276317 --format="table(name,diskSizeGb,storageBytes.size(),creationTimestamp.date())"`

### 3. Dry run and confirm

```bash
.claude/skills/vm-tracker/create_vm.sh --dry-run <flags> [VM_NAME]
```

Show the plan lines (snapshot, boot disk, machine) and get
a go-ahead before creating anything. If the VM or a disk already exists, do
**not** delete anything: tell the user and suggest another VM name.

### 4. Create

Re-run without `--dry-run` (timeout ≥ 5 min); take the IP from the `RESULT`
line. If it fails part-way, list leftovers
(`gcloud compute disks list --filter="name~^<VM_NAME>"`) and leave cleanup to
the user.

### 5. Update ~/.ssh/config

The ssh alias is `tracker` (for another VM name use that name; ask if they want
`tracker` repointed instead):

```bash
python3 -I .claude/skills/vm-tracker/update_ssh_config.py <alias> <ip> --template station
```

It edits `HostName` in place if the alias exists (backup at `~/.ssh/config.bak`);
otherwise it appends a new block cloned from the existing `station` entry (same
`User aidev`, host-key and keepalive options) with the new `HostName`. The new
instance has new host keys and Google often reuses a freed IP, so clear stale
entries:

```bash
ssh-keygen -R <alias>; ssh-keygen -R <old-ip>; ssh-keygen -R <new-ip>
```

### 6. Verify and report

Wait for sshd (retry `ssh -o BatchMode=yes -o ConnectTimeout=8 <alias> true`
for a couple of minutes; the first boot from a snapshot can be slow), then
check `df -h /`, `mountpoint /mnt/vinolab` and `sudo -n true`. Report: VM name, machine,
snapshot used, public IP, and the ssh
config change. The snapshot's fstab mounts bucket `vinolab` at `/mnt/vinolab` (gcsfuse,
`worker-vm` has `storage.objectUser` on it); if `systemctl is-system-running` says
`degraded`, check `systemctl --failed` and report it. The snapshot does not
refresh `~/.config/rclone/rclone.conf`; that file is baked into the snapshot
(edit it on a VM and re-snapshot to change it). Say that there is no scratch
disk yet (`vm-tracker scratch add`, default 150 GB; `takeout-fetch` needs it).
Remind the user the VM bills until stopped or retired.

## Scratch disk

`/mnt/scratch` holds fetched Takeout zips and extracted batches. Ask the user how
big whenever one is being added, with **150 GB** as the default (AskUserQuestion,
150 GB first), unless they already said a size.

```bash
.claude/skills/vm-tracker/scratch_disk.sh status
.claude/skills/vm-tracker/scratch_disk.sh add [GB] [--dry-run]
.claude/skills/vm-tracker/scratch_disk.sh remove            # shows what would go
.claude/skills/vm-tracker/scratch_disk.sh remove --yes      # unmount, detach, DELETE
```

- `add` needs the VM RUNNING and `ssh tracker` working. It creates the disk,
  attaches it, formats it (only if blank) and mounts it with mode 1777. It writes
  one fixed line to the VM's `/etc/fstab`
  (`/dev/disk/by-id/google-scratch /mnt/scratch ext4 defaults,nofail,...`), which
  is never removed: the device path is the same for every scratch disk and
  `nofail` makes a boot without one harmless.
- `remove` without `--yes` only prints the usage and top-level contents. Show that
  to the user, say that **everything on the disk is lost and cannot be recovered
  (no snapshot)**, and get an explicit go-ahead before `--yes`. It refuses, and
  changes nothing, if `/mnt/scratch` is busy (a shell `cd`'d in, a running
  `takeout-fetch`); tell the user what to stop.
- Only one scratch disk per VM. `add` refuses if one is attached.

## Stop

Use when the user is done for now but wants to keep the VM (cheaper than leaving
it running; boot disk still bills, scratch must not).

```bash
.claude/skills/vm-tracker/stop_vm.sh [--vm NAME]          # plan only
.claude/skills/vm-tracker/stop_vm.sh [--vm NAME] --yes
```

Show the plan (it includes the scratch disk contents that will be lost), get a
go-ahead, then run with `--yes`. Afterwards say the public IP is released: on the
next `gcloud compute instances start` the IP changes, so re-run
`update_ssh_config.py` and clear `known_hosts` as in Create step 5.

## Retire

Use when the VM will sit idle: snapshots cost far less than a VM plus disks.

### 1. Dry run and confirm

```bash
.claude/skills/vm-tracker/retire_vm.sh --dry-run [VM_NAME]
```

Options: `--no-stop`, `--keep-vm`. It fails if today's
`tracker-base-<yyyymmdd>` already exists. Show the plan and state clearly that
the VM **and every disk, including any attached scratch disk, will be deleted
and only the boot disk is snapshotted**. Get an explicit go-ahead, and suggest the
user copy anything wanted out of `/mnt/scratch` first.

Running on the VM being retired: the script snapshots without stopping and
this session dies when the VM is deleted; recommend running from elsewhere.

### 2. Retire

Re-run without `--dry-run` (timeout 10 min). The VM is deleted only after the
snapshot is `READY`.

### 3. Report

Snapshot name and size (`gcloud compute snapshots describe NAME
--format="value(storageBytes)"`; sizes are incremental after the first), that
the VM and disks are gone, and the restore command (`vm-tracker create`, which
picks the newest snapshot). Older `tracker-base-*` snapshots still cost
storage; list them and leave deleting to the user.
