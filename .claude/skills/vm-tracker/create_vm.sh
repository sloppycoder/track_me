#!/usr/bin/env bash
# Create the tracker VM from the newest tracker-base-<yyyymmdd> snapshot, then
# point the `tracker` ssh alias at it. Boot disk only (see add_scratch.sh).
#
# usage: create_vm.sh [--machine-type TYPE] [--snapshot NAME]
#   defaults: e2-standard-2 (2 vCPU / 8 GB), newest tracker-base-* snapshot
set -euo pipefail
. "$(dirname "$0")/lib.sh"

MACHINE="e2-standard-2"
SNAPSHOT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --machine-type) MACHINE="$2"; shift 2 ;;
    --snapshot) SNAPSHOT="$2"; shift 2 ;;
    *) die "usage: create_vm.sh [--machine-type TYPE] [--snapshot NAME]" ;;
  esac
done

[[ -z "$(vm_status)" ]] || die "instance ${VM} already exists"
if [[ -z "${SNAPSHOT}" ]]; then
  SNAPSHOT="$(gcloud compute snapshots list --project="${PROJECT}" \
    --filter="name~^tracker-base-[0-9]+$ AND status=READY" \
    --sort-by=~creationTimestamp --limit=1 --format='value(name)')"
  [[ -n "${SNAPSHOT}" ]] || die "no READY snapshot named tracker-base-<yyyymmdd>"
fi

echo ">> Creating ${VM} (${MACHINE}) from ${SNAPSHOT}..."
gcloud compute disks create "${VM}" "${GC[@]}" --source-snapshot="${SNAPSHOT}" \
  --type=pd-balanced || echo ">> create reported an error; checking whether it went through..."
poll 36 has_status disks "${VM}" READY || die "boot disk ${VM} did not become READY"

gcloud compute instances create "${VM}" "${GC[@]}" --machine-type="${MACHINE}" \
  --disk="name=${VM},boot=yes,auto-delete=yes,device-name=${VM}" \
  --network-interface=network=default,subnet=default,network-tier=PREMIUM \
  --service-account="worker-vm@${PROJECT}.iam.gserviceaccount.com" \
  --scopes=cloud-platform || echo ">> create reported an error; checking whether it went through..."
poll 36 has_status instances "${VM}" RUNNING || die "instance ${VM} is not RUNNING"

refresh_ssh
echo ">> ${VM} is up: ssh ${VM}   (no scratch disk yet: add_scratch.sh)"
