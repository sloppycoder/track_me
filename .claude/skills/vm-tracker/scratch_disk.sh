#!/usr/bin/env bash
#
# Add, remove or inspect the scratch disk of the running tracker VM. The disk
# is hot-attached, formatted and mounted at /mnt/scratch; on removal it is
# unmounted, detached and DELETED. /etc/fstab gets ONE fixed line, written by
# add if missing and never removed: it names the device path
# /dev/disk/by-id/google-scratch (stable across disks, no UUID), with nofail so a
# boot without a scratch disk is fine and a still-attached one mounts on reboot.
# It is never snapshotted (retire_vm.sh only snapshots the boot disk), so it only
# costs money while it exists.
# Driven by the vm-tracker skill (see SKILL.md); runnable by hand too.
#
# Usage:
#   scratch_disk.sh add [GB] [options]       create + attach + format + mount (default 150)
#   scratch_disk.sh remove [options]         without --yes only shows what would go
#   scratch_disk.sh status [options]
#
# Options:
#   --vm NAME        VM name (default tracker); the disk is named <vm>-scratch
#   --host HOST      ssh host used to format/mount/unmount (default: the VM name)
#   --yes            really remove (unmount, detach, delete); data is lost
#   --dry-run        add: validate and show the plan only
#   --zone ZONE      default us-west1-b
#   --project PROJECT  default vino9-276317
#
# add/remove need the VM RUNNING and reachable over ssh (aidev, passwordless sudo).
# remove refuses if /mnt/scratch is busy (umount fails) and changes nothing.
# On success the last line is:  RESULT <vm-name> <disk> added|removed|none

set -euo pipefail

PROJECT="vino9-276317"
ZONE="us-west1-b"
VM_NAME="tracker"
HOST=""
ACTION=""
SIZE_GB=""
YES=0
DRY_RUN=0
DISK_TYPE="pd-balanced"
DEVICE_NAME="scratch"

die() { echo "ERROR: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vm) VM_NAME="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    --yes) YES=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --zone) ZONE="$2"; shift 2 ;;
    --project) PROJECT="$2"; shift 2 ;;
    -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
    -*) die "unknown option $1" ;;
    *) if [[ -z "${ACTION}" ]]; then ACTION="$1"; else SIZE_GB="$1"; fi; shift ;;
  esac
done
[[ "${ACTION}" =~ ^(add|remove|status)$ ]] || die "usage: scratch_disk.sh add [GB] | remove | status"
HOST="${HOST:-${VM_NAME}}"
DISK="${VM_NAME}-scratch"
GC=(--project="${PROJECT}" --zone="${ZONE}")

rsh() { ssh -o BatchMode=yes -o ConnectTimeout=10 "${HOST}" "$@"; }

vm_status() {
  gcloud compute instances describe "${VM_NAME}" "${GC[@]}" --format="value(status)" \
    2>/dev/null || true
}
# Name of the disk attached with device name "scratch" (empty when none).
attached_disk() {
  gcloud compute instances describe "${VM_NAME}" "${GC[@]}" --format=json 2>/dev/null \
    | python3 -I -c 'import json, sys
for d in json.load(sys.stdin).get("disks", []):
    if d.get("deviceName") == "scratch":
        print(d["source"].rsplit("/", 1)[1])' || true
}
disk_exists() {
  gcloud compute disks describe "${DISK}" "${GC[@]}" --format="value(name)" >/dev/null 2>&1
}
# gcloud's long "wait for operation" call is sometimes dropped by a proxy
# (ProxyError) even though the operation proceeds, so every mutating command is
# followed by a poll of its end state with short describe calls.
poll() {  # poll <tries> <command...>  (5 s apart)
  local tries="$1" i; shift
  for i in $(seq 1 "${tries}"); do "$@" && return 0; sleep 5; done
  return 1
}
disk_ready() { [[ "$(gcloud compute disks describe "${DISK}" "${GC[@]}" --format='value(status)' 2>/dev/null || true)" == "READY" ]]; }
disk_gone() { ! disk_exists; }
is_attached() { [[ "$(attached_disk)" == "${DISK}" ]]; }
not_attached() { [[ -z "$(attached_disk)" ]]; }

VM_STATUS="$(vm_status)"
[[ -n "${VM_STATUS}" ]] || die "instance ${VM_NAME} not found in ${PROJECT}/${ZONE}"
ATTACHED="$(attached_disk)"

# ---------------------------------------------------------------- status ----
if [[ "${ACTION}" == "status" ]]; then
  echo "VM ${VM_NAME}: ${VM_STATUS}"
  if [[ -n "${ATTACHED}" ]]; then
    SIZE="$(gcloud compute disks describe "${ATTACHED}" "${GC[@]}" --format='value(sizeGb)')"
    echo "scratch disk: ${ATTACHED} (${SIZE} GB), attached as device 'scratch'"
    if [[ "${VM_STATUS}" == "RUNNING" ]]; then
      rsh 'if mountpoint -q /mnt/scratch; then df -h /mnt/scratch | tail -1; else echo "/mnt/scratch is NOT mounted. Mount it: sudo mount /mnt/scratch"; fi' \
        || echo "(could not ssh to ${HOST})"
    fi
    echo "RESULT ${VM_NAME} ${ATTACHED} present"
  else
    echo "scratch disk: none"
    echo "RESULT ${VM_NAME} ${DISK} none"
  fi
  exit 0
fi

# ------------------------------------------------------------------- add ----
if [[ "${ACTION}" == "add" ]]; then
  SIZE_GB="${SIZE_GB:-150}"
  [[ "${SIZE_GB}" =~ ^[0-9]+$ ]] && (( SIZE_GB >= 10 )) || die "size must be a number of GB >= 10"
  [[ "${VM_STATUS}" == "RUNNING" ]] \
    || die "${VM_NAME} is ${VM_STATUS}; start it first (add formats and mounts over ssh)"
  [[ -z "${ATTACHED}" ]] || die "${VM_NAME} already has scratch disk ${ATTACHED}; remove it first"
  disk_exists && die "disk ${DISK} already exists (not attached); delete it or attach it by hand"
  rsh true || die "cannot ssh to ${HOST}"
  echo ">> Plan: ${DISK} (${SIZE_GB} GB ${DISK_TYPE}) -> ${VM_NAME}:/mnt/scratch"
  if [[ "${DRY_RUN}" -eq 1 ]]; then echo ">> Dry run: nothing created."; exit 0; fi

  echo ">> Creating disk ${DISK}..."
  gcloud compute disks create "${DISK}" "${GC[@]}" --size="${SIZE_GB}" --type="${DISK_TYPE}" \
    || echo ">> create reported an error; checking whether it went through..."
  poll 24 disk_ready || die "disk ${DISK} was not created"

  echo ">> Attaching to ${VM_NAME}..."
  gcloud compute instances attach-disk "${VM_NAME}" "${GC[@]}" --disk="${DISK}" \
    --device-name="${DEVICE_NAME}" \
    || echo ">> attach reported an error; checking whether it went through..."
  poll 24 is_attached || die "disk ${DISK} did not attach (it still exists; run: scratch_disk.sh remove --yes)"

  echo ">> Formatting and mounting on ${HOST}..."
  rsh 'sudo bash -s' <<'REMOTE' || die "attached ${DISK} but formatting/mounting failed (retry add after 'remove --yes')"
set -euo pipefail
DEV=/dev/disk/by-id/google-scratch
for i in $(seq 1 30); do [ -e "$DEV" ] && break; sleep 1; done
[ -e "$DEV" ] || { echo "device $DEV did not appear" >&2; exit 1; }
if mountpoint -q /mnt/scratch; then echo "/mnt/scratch is already a mountpoint" >&2; exit 1; fi
blkid "$DEV" >/dev/null 2>&1 || mkfs.ext4 -q -F -m 0 "$DEV"
mkdir -p /mnt/scratch
grep -q '^/dev/disk/by-id/google-scratch ' /etc/fstab || \
  echo '/dev/disk/by-id/google-scratch /mnt/scratch ext4 defaults,nofail,x-systemd.device-timeout=5s 0 2' >> /etc/fstab
systemctl daemon-reload
mount /mnt/scratch
chmod 1777 /mnt/scratch
df -h /mnt/scratch | tail -1
REMOTE
  echo ">> Done."
  echo "RESULT ${VM_NAME} ${DISK} added"
  exit 0
fi

# ---------------------------------------------------------------- remove ----
if [[ -z "${ATTACHED}" ]]; then
  if disk_exists; then
    echo ">> ${DISK} exists but is not attached to ${VM_NAME}."
    ATTACHED="${DISK}"   # fall through: nothing to unmount, just delete
  else
    echo ">> ${VM_NAME} has no scratch disk."
    echo "RESULT ${VM_NAME} ${DISK} none"
    exit 0
  fi
fi
SIZE="$(gcloud compute disks describe "${ATTACHED}" "${GC[@]}" --format='value(sizeGb)')"
echo ">> ${VM_NAME} (${VM_STATUS}) scratch disk ${ATTACHED} (${SIZE} GB):"
RUNNING_ATTACHED=0
[[ "${VM_STATUS}" == "RUNNING" && "$(attached_disk)" == "${ATTACHED}" ]] && RUNNING_ATTACHED=1
if (( RUNNING_ATTACHED )); then
  rsh 'if mountpoint -q /mnt/scratch; then df -h /mnt/scratch | tail -1; echo "top level:"; ls -A /mnt/scratch | head -15; else echo "(not mounted)"; fi' \
    || die "cannot ssh to ${HOST}; refusing to remove a disk I cannot unmount"
else
  echo "   VM is ${VM_STATUS}: nothing to unmount; the disk is detached and deleted"
fi
echo ">> Will: unmount, detach, DELETE ${ATTACHED} (all data on it is lost, no snapshot exists)"
if [[ "${YES}" -ne 1 ]]; then
  echo ">> Not removing: re-run with --yes to proceed."
  echo "RESULT ${VM_NAME} ${ATTACHED} present"
  exit 0
fi

if (( RUNNING_ATTACHED )); then
  echo ">> Unmounting on ${HOST}..."
  rsh 'sudo bash -s' <<'REMOTE' || die "could not unmount /mnt/scratch (busy?); nothing was removed"
set -euo pipefail
if mountpoint -q /mnt/scratch; then
  umount /mnt/scratch || { echo "umount failed: /mnt/scratch is busy" >&2; exit 1; }
fi
REMOTE
fi

if [[ "$(attached_disk)" == "${ATTACHED}" ]]; then
  echo ">> Detaching ${ATTACHED}..."
  gcloud compute instances detach-disk "${VM_NAME}" "${GC[@]}" --disk="${ATTACHED}" \
    || echo ">> detach reported an error; checking whether it went through..."
  poll 24 not_attached || die "${ATTACHED} is still attached"
fi
echo ">> Deleting ${ATTACHED}..."
gcloud compute disks delete "${ATTACHED}" "${GC[@]}" --quiet \
  || echo ">> delete reported an error; checking whether it went through..."
DISK="${ATTACHED}" poll 24 disk_gone || die "${ATTACHED} still exists"
echo ">> Done."
echo "RESULT ${VM_NAME} ${ATTACHED} removed"
