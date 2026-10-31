#!/usr/bin/env bash
#
# Snapshot the boot disk of the tracker VM as tracker-base-<yyyymmdd>, then
# delete the VM and ALL its disks (the scratch disk is NOT preserved).
# Driven by the vm-tracker skill. Restore with create_vm.sh.
#
# Usage:
#   retire_vm.sh [options] [VM_NAME]          (VM_NAME defaults to "tracker")
#
# Options:
#   --no-stop       snapshot while running (crash-consistent); implied when run
#                   on the VM itself
#   --keep-vm       take the snapshot but delete nothing
#   --zone ZONE     default us-west1-b
#   --project PROJECT  default vino9-276317
#   --dry-run       show the plan, change nothing
#
# The snapshot is named tracker-base-<YYYYMMDD> (UTC) and inherits the disk's
# guest-OS features (UEFI), which a snapshot of a hand-built disk would lack.

set -euo pipefail

PROJECT="vino9-276317"
ZONE="us-west1-b"
VM_NAME="tracker"
NO_STOP=0
KEEP_VM=0
DRY_RUN=0

die() { echo "ERROR: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-stop) NO_STOP=1; shift ;;
    --keep-vm) KEEP_VM=1; shift ;;
    --zone) ZONE="$2"; shift 2 ;;
    --project) PROJECT="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    -*) die "unknown option $1" ;;
    *) VM_NAME="$1"; shift ;;
  esac
done
SNAP="tracker-base-$(date -u +%Y%m%d)"
GC=(--project="${PROJECT}")

SELF="$(curl -sf -m 2 -H 'Metadata-Flavor: Google' \
  http://metadata.google.internal/computeMetadata/v1/instance/name || true)"
if [[ "${SELF}" == "${VM_NAME}" && "${NO_STOP}" -eq 0 ]]; then
  echo ">> Running on ${VM_NAME} itself: snapshotting without stopping."
  NO_STOP=1
fi

STATUS="$(gcloud compute instances describe "${VM_NAME}" "${GC[@]}" \
  --zone="${ZONE}" --format="value(status)" 2>/dev/null)" \
  || die "instance ${VM_NAME} not found in ${PROJECT}/${ZONE}"
# "<disk-name> <boot True|False>" per attached disk.
DISKS="$(gcloud compute instances describe "${VM_NAME}" "${GC[@]}" \
  --zone="${ZONE}" --format="json(disks)" \
  | python3 -Ic 'import json,sys
for d in json.load(sys.stdin)["disks"]:
    print(d["source"].rsplit("/", 1)[1], d.get("boot", False))')"
BOOT_DISK="$(awk '$2=="True"{print $1}' <<<"${DISKS}")"
OTHERS="$(awk '$2!="True"{print $1}' <<<"${DISKS}" | paste -sd' ' -)"
[[ -n "${BOOT_DISK}" ]] || die "no boot disk found on ${VM_NAME}"
gcloud compute snapshots describe "${SNAP}" "${GC[@]}" >/dev/null 2>&1 \
  && die "snapshot ${SNAP} already exists (one per day; delete it or wait)"

echo ">> Plan for ${VM_NAME} (${STATUS}) in ${PROJECT}/${ZONE}:"
echo "   ${BOOT_DISK} -> ${SNAP}"
[[ "${NO_STOP}" -eq 0 && "${STATUS}" == "RUNNING" ]] && echo "   stop VM first"
if [[ "${KEEP_VM}" -eq 1 ]]; then
  echo "   keep VM and disks"
else
  echo "   then DELETE instance ${VM_NAME} and all its disks" \
       "(not snapshotted, lost: ${OTHERS:-none})"
fi
if [[ "${DRY_RUN}" -eq 1 ]]; then
  echo ">> Dry run: nothing changed."
  exit 0
fi

if [[ "${NO_STOP}" -eq 0 && "${STATUS}" == "RUNNING" ]]; then
  echo ">> Stopping ${VM_NAME}..."
  gcloud compute instances stop "${VM_NAME}" "${GC[@]}" --zone="${ZONE}"
fi
echo ">> Snapshotting ${BOOT_DISK} -> ${SNAP}..."
gcloud compute disks snapshot "${BOOT_DISK}" "${GC[@]}" --zone="${ZONE}" \
  --snapshot-names="${SNAP}" --storage-location=us
ST="$(gcloud compute snapshots describe "${SNAP}" "${GC[@]}" --format="value(status)")"
[[ "${ST}" == "READY" ]] || die "snapshot ${SNAP} is ${ST}; not deleting the VM"
echo ">> Snapshot ${SNAP} READY."

if [[ "${KEEP_VM}" -eq 1 ]]; then
  echo "RESULT ${VM_NAME} kept ${SNAP}"
  exit 0
fi
echo ">> Deleting ${VM_NAME} and its disks..."
echo "RESULT ${VM_NAME} deleted ${SNAP}"
gcloud compute instances delete "${VM_NAME}" "${GC[@]}" --zone="${ZONE}" \
  --delete-disks=all --quiet
