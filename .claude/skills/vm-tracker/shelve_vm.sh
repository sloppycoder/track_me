#!/usr/bin/env bash
# Shelve the tracker VM (put it away until needed again): stop it, snapshot its boot disk as tracker-base-<yyyymmdd>
# (UTC date; the next create_vm.sh picks the newest), then delete the VM. The boot
# disk is deleted with the VM; any attached scratch disk is deleted too and is NOT
# snapshotted. The VM is deleted only after the snapshot is READY.
#
# usage: shelve_vm.sh [--yes]       (without --yes only shows the plan)
set -euo pipefail
. "$(dirname "$0")/lib.sh"

STATUS="$(vm_status)"
[[ -n "${STATUS}" ]] || die "instance ${VM} not found"
BOOT="$(attached_disk boot)"
SNAP="${SNAPSHOT_OUT:-tracker-base-$(date -u +%Y%m%d)}"   # SNAPSHOT_OUT: for tests
gcloud compute snapshots describe "${SNAP}" --project="${PROJECT}" >/dev/null 2>&1 \
  && die "snapshot ${SNAP} already exists (one per day); delete it first if you mean to replace it"
SCRATCH="$(attached_disk scratch)"

echo ">> Will: stop ${VM} (${STATUS}); snapshot ${BOOT} -> ${SNAP}; then delete ${VM}"
echo "   and its disks${SCRATCH:+ (including scratch ${SCRATCH}, whose data is lost)}."
[[ "${1:-}" == "--yes" ]] || { echo ">> Not doing it: re-run with --yes."; exit 0; }

if [[ "${STATUS}" != "TERMINATED" ]]; then
  echo ">> Stopping ${VM}..."
  gcloud compute instances stop "${VM}" "${GC[@]}" \
    || echo ">> stop reported an error; checking whether it went through..."
  poll 60 has_status instances "${VM}" TERMINATED || die "${VM} did not stop"
fi
echo ">> Snapshotting ${BOOT} -> ${SNAP}..."
gcloud compute disks snapshot "${BOOT}" "${GC[@]}" --snapshot-names="${SNAP}" --storage-location=us \
  || echo ">> snapshot reported an error; checking whether it went through..."
poll 120 snapshot_ready "${SNAP}" || die "snapshot ${SNAP} is not READY; ${VM} was NOT deleted"
echo ">> ${SNAP} is READY. Deleting ${VM} and its disks..."
gcloud compute instances delete "${VM}" "${GC[@]}" --delete-disks=all --quiet \
  || echo ">> delete reported an error; checking whether it went through..."
poll 36 gone instances "${VM}" || die "${VM} still exists"
echo ">> Shelved. Bring it back with create_vm.sh (uses the newest tracker-base-*)."
