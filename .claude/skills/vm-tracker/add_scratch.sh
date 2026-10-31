#!/usr/bin/env bash
# Add a scratch disk to the tracker VM: create, attach, format (only if blank),
# add the fixed /etc/fstab line (once), mount at /mnt/scratch. Starts the VM first
# if it is stopped. The disk is never snapshotted; del_scratch.sh deletes it.
#
# usage: add_scratch.sh [GB]      (default 150)
set -euo pipefail
. "$(dirname "$0")/lib.sh"

SIZE="${1:-150}"
[[ "${SIZE}" =~ ^[0-9]+$ ]] && ((SIZE >= 10)) || die "usage: add_scratch.sh [GB >= 10]"
DISK="${VM}-scratch"

STATUS="$(vm_status)"
[[ -n "${STATUS}" ]] || die "instance ${VM} not found"
[[ -z "$(attached_disk scratch)" ]] || die "${VM} already has a scratch disk"
[[ -z "$(status_of disks "${DISK}")" ]] || die "disk ${DISK} already exists (not attached)"

if [[ "${STATUS}" != "RUNNING" ]]; then
  echo ">> Starting ${VM} (${STATUS})..."
  gcloud compute instances start "${VM}" "${GC[@]}" \
    || echo ">> start reported an error; checking whether it went through..."
  poll 36 has_status instances "${VM}" RUNNING || die "${VM} did not start"
  refresh_ssh
fi

echo ">> Creating ${DISK} (${SIZE} GB) and attaching to ${VM}..."
gcloud compute disks create "${DISK}" "${GC[@]}" --size="${SIZE}" --type=pd-balanced \
  || echo ">> create reported an error; checking whether it went through..."
poll 36 has_status disks "${DISK}" READY || die "${DISK} did not become READY"
gcloud compute instances attach-disk "${VM}" "${GC[@]}" --disk="${DISK}" --device-name=scratch \
  || echo ">> attach reported an error; checking whether it went through..."
poll 36 scratch_is "${DISK}" || die "${DISK} did not attach"

echo ">> Formatting and mounting..."
ssh -o BatchMode=yes "${VM}" 'sudo bash -s' <<'REMOTE'
set -euo pipefail
DEV=/dev/disk/by-id/google-scratch
for i in $(seq 1 30); do [ -e "$DEV" ] && break; sleep 1; done
[ -e "$DEV" ] || { echo "device $DEV did not appear" >&2; exit 1; }
blkid "$DEV" >/dev/null 2>&1 || mkfs.ext4 -q -F -m 0 "$DEV"
mkdir -p /mnt/scratch
grep -q '^/dev/disk/by-id/google-scratch ' /etc/fstab || \
  echo '/dev/disk/by-id/google-scratch /mnt/scratch ext4 defaults,nofail,x-systemd.device-timeout=5s 0 2' >> /etc/fstab
systemctl daemon-reload
mountpoint -q /mnt/scratch || mount /mnt/scratch
chmod 1777 /mnt/scratch
df -h /mnt/scratch | tail -1
REMOTE
echo ">> Scratch disk ready: ${DISK} at /mnt/scratch"
