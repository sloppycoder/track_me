#!/usr/bin/env bash
#
# Stop the tracker VM without paying for a forgotten scratch disk: removes the
# scratch disk first (scratch_disk.sh remove --yes), then stops the VM. The boot
# disk is kept, so `gcloud compute instances start` brings it back (new public
# IP: re-run update_ssh_config.py). Driven by the vm-tracker skill.
#
# Usage:
#   stop_vm.sh [--vm NAME] [--zone ZONE] [--project PROJECT] [--yes]
#
# Without --yes it only shows the plan. On success the last line is:
#   RESULT <vm-name> stopped|already-stopped

set -euo pipefail

PROJECT="vino9-276317"
ZONE="us-west1-b"
VM_NAME="tracker"
YES=0
HERE="$(cd "$(dirname "$0")" && pwd)"

die() { echo "ERROR: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vm) VM_NAME="$2"; shift 2 ;;
    --zone) ZONE="$2"; shift 2 ;;
    --project) PROJECT="$2"; shift 2 ;;
    --yes) YES=1; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) die "unknown argument $1" ;;
  esac
done
GC=(--project="${PROJECT}" --zone="${ZONE}")

STATUS="$(gcloud compute instances describe "${VM_NAME}" "${GC[@]}" \
  --format='value(status)' 2>/dev/null)" \
  || die "instance ${VM_NAME} not found in ${PROJECT}/${ZONE}"
if [[ "${STATUS}" == "TERMINATED" ]]; then
  echo ">> ${VM_NAME} is already stopped."
  # a scratch disk left attached to a stopped VM still bills
  "${HERE}/scratch_disk.sh" status --vm "${VM_NAME}" "${GC[@]}" 2>&1 | grep -v "^RESULT" || true
  echo "RESULT ${VM_NAME} already-stopped"
  exit 0
fi

SCRATCH_ARGS=(--vm "${VM_NAME}" --zone "${ZONE}" --project "${PROJECT}")
echo ">> Plan for ${VM_NAME} (${STATUS}):"
"${HERE}/scratch_disk.sh" remove "${SCRATCH_ARGS[@]}" | grep -v "^RESULT" | sed 's/^/   /'
echo "   then stop ${VM_NAME} (boot disk kept; public IP will change on start)"
if [[ "${YES}" -ne 1 ]]; then
  echo ">> Not stopping: re-run with --yes."
  exit 0
fi

"${HERE}/scratch_disk.sh" remove "${SCRATCH_ARGS[@]}" --yes | tail -3
echo ">> Stopping ${VM_NAME}..."
gcloud compute instances stop "${VM_NAME}" "${GC[@]}" \
  || echo ">> stop reported an error; checking whether it went through..."
for _ in $(seq 1 60); do
  [[ "$(gcloud compute instances describe "${VM_NAME}" "${GC[@]}" --format='value(status)' 2>/dev/null || true)" == "TERMINATED" ]] && break
  sleep 5
done
STATUS="$(gcloud compute instances describe "${VM_NAME}" "${GC[@]}" --format='value(status)')"
[[ "${STATUS}" == "TERMINATED" ]] || die "${VM_NAME} is ${STATUS}, expected TERMINATED"
echo "RESULT ${VM_NAME} stopped"
