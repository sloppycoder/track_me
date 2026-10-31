#!/usr/bin/env bash
# Delete the tracker VM's scratch disk. Stops the VM first (so nothing is mounted
# or in use), detaches the disk and deletes it. ALL DATA ON IT IS LOST (it is never
# snapshotted). The VM is left STOPPED; add_scratch.sh starts it again. The public
# IP changes on the next start.
#
# usage: del_scratch.sh [--yes]     (without --yes only shows what would happen)
set -euo pipefail
. "$(dirname "$0")/lib.sh"

DISK="${VM}-scratch"
STATUS="$(vm_status)"
[[ -n "${STATUS}" ]] || die "instance ${VM} not found"
SIZE="$(gcloud compute disks describe "${DISK}" "${GC[@]}" --format='value(sizeGb)' 2>/dev/null || true)"
if [[ -z "${SIZE}" ]]; then echo ">> ${VM} has no scratch disk (${DISK})."; exit 0; fi

echo ">> Will: stop ${VM} (${STATUS}), detach and DELETE ${DISK} (${SIZE} GB)."
echo "   All data on it is lost; there is no snapshot. ${VM} stays stopped."
[[ "${1:-}" == "--yes" ]] || { echo ">> Not doing it: re-run with --yes."; exit 0; }

if [[ "${STATUS}" != "TERMINATED" ]]; then
  echo ">> Stopping ${VM}..."
  gcloud compute instances stop "${VM}" "${GC[@]}" \
    || echo ">> stop reported an error; checking whether it went through..."
  poll 60 has_status instances "${VM}" TERMINATED || die "${VM} did not stop"
fi
if [[ -n "$(attached_disk scratch)" ]]; then
  echo ">> Detaching ${DISK}..."
  gcloud compute instances detach-disk "${VM}" "${GC[@]}" --disk="${DISK}" \
    || echo ">> detach reported an error; checking whether it went through..."
  poll 36 scratch_none || die "${DISK} is still attached"
fi
echo ">> Deleting ${DISK}..."
gcloud compute disks delete "${DISK}" "${GC[@]}" --quiet \
  || echo ">> delete reported an error; checking whether it went through..."
poll 36 gone disks "${DISK}" || die "${DISK} still exists"
echo ">> Scratch disk deleted. ${VM} is stopped."
