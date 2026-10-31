#!/usr/bin/env bash
#
# Create the "tracker" processing VM from the newest tracker-base-* boot
# snapshot, plus an optional blank scratch disk. Driven by the vm-tracker
# skill (see SKILL.md); runnable by hand too.
#
# Usage:
#   create_vm.sh [options] [VM_NAME]          (VM_NAME defaults to "tracker")
#
# Options:
#   --machine-type TYPE   predefined type (default e2-standard-2)
#   --cpus N --memory GB  custom machine (e2 family unless --family given)
#   --family FAMILY       custom machine family (default e2)
#   --spot                Spot VM (cheaper, can be preempted)
#   --snapshot NAME       boot snapshot (default: newest tracker-base-*)
#   --boot-size GB        boot disk size (default: snapshot's source size)
#   --scratch-size GB     blank scratch disk, mounted at /mnt/scratch (default 150)
#   --no-scratch          do not create a scratch disk
#   --zone ZONE           default us-west1-b
#   --project PROJECT     default vino9-276317
#   --dry-run             resolve and validate everything, create nothing
#
# The scratch disk is blank; a startup script formats it (ext4, only if it
# has no filesystem yet) and mounts it at /mnt/scratch via fstab (nofail).
# aidev's ssh key travels inside the snapshot, so no ssh-keys metadata is set.
#
# On success the last line of stdout is:  RESULT <vm-name> <status> <ip>

set -euo pipefail

PROJECT="vino9-276317"
ZONE="us-west1-b"
MACHINE_TYPE=""
CPUS=""
MEMORY_GB=""
FAMILY="e2"
SPOT=0
SNAPSHOT=""
BOOT_DISK_SIZE_GB=""
SCRATCH_SIZE_GB=150
DRY_RUN=0
VM_NAME="tracker"

DISK_TYPE="pd-balanced"
SERVICE_ACCOUNT="worker-vm@vino9-276317.iam.gserviceaccount.com"
DEFAULT_MACHINE_TYPE="e2-standard-2"

die() { echo "ERROR: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --machine-type) MACHINE_TYPE="$2"; shift 2 ;;
    --cpus) CPUS="$2"; shift 2 ;;
    --memory) MEMORY_GB="$2"; shift 2 ;;
    --family) FAMILY="$2"; shift 2 ;;
    --spot) SPOT=1; shift ;;
    --snapshot) SNAPSHOT="$2"; shift 2 ;;
    --boot-size) BOOT_DISK_SIZE_GB="$2"; shift 2 ;;
    --scratch-size) SCRATCH_SIZE_GB="$2"; shift 2 ;;
    --no-scratch) SCRATCH_SIZE_GB=0; shift ;;
    --zone) ZONE="$2"; shift 2 ;;
    --project) PROJECT="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
    -*) die "unknown option $1" ;;
    *) VM_NAME="$1"; shift ;;
  esac
done
BOOT_DISK="${VM_NAME}"
SCRATCH_DISK="${VM_NAME}-scratch"

# --- Machine type -----------------------------------------------------------
if [[ -n "${CPUS}" || -n "${MEMORY_GB}" ]]; then
  [[ -n "${MACHINE_TYPE}" ]] && die "--machine-type conflicts with --cpus/--memory"
  [[ -n "${CPUS}" && -n "${MEMORY_GB}" ]] || die "--cpus and --memory go together"
  MACHINE_ARGS=(--custom-vm-type="${FAMILY}" --custom-cpu="${CPUS}"
                --custom-memory="${MEMORY_GB}GB")
  MACHINE_DESC="custom ${FAMILY} ${CPUS} vCPU / ${MEMORY_GB} GB"
else
  MACHINE_TYPE="${MACHINE_TYPE:-${DEFAULT_MACHINE_TYPE}}"
  gcloud compute machine-types describe "${MACHINE_TYPE}" \
    --project="${PROJECT}" --zone="${ZONE}" --format="value(name)" \
    >/dev/null 2>&1 || die "machine type ${MACHINE_TYPE} is not offered in ${ZONE}"
  MACHINE_ARGS=(--machine-type="${MACHINE_TYPE}")
  MACHINE_DESC="${MACHINE_TYPE}"
fi

# --- Snapshot ---------------------------------------------------------------
if [[ -z "${SNAPSHOT}" ]]; then
  SNAPSHOT="$(gcloud compute snapshots list --project="${PROJECT}" \
    --filter="name~^tracker-base-[0-9]+$" --sort-by=~creationTimestamp \
    --limit=1 --format="value(name)")"
  [[ -n "${SNAPSHOT}" ]] || die "no snapshot named tracker-base-<yyyymmdd>; pass --snapshot"
fi
read -r SNAP_STATUS BOOT_SRC_GB < <(gcloud compute snapshots describe "${SNAPSHOT}" \
  --project="${PROJECT}" --format="value(status,diskSizeGb)") \
  || die "snapshot ${SNAPSHOT} not found in ${PROJECT}"
[[ "${SNAP_STATUS}" == "READY" ]] || die "snapshot ${SNAPSHOT} is ${SNAP_STATUS}"
BOOT_DISK_SIZE_GB="${BOOT_DISK_SIZE_GB:-${BOOT_SRC_GB}}"
(( BOOT_DISK_SIZE_GB >= BOOT_SRC_GB )) \
  || die "--boot-size ${BOOT_DISK_SIZE_GB} < snapshot source size ${BOOT_SRC_GB}"
echo "   boot: ${SNAPSHOT} -> ${BOOT_DISK} (${BOOT_DISK_SIZE_GB} GB)"
if (( SCRATCH_SIZE_GB > 0 )); then
  echo "   scratch: blank ${SCRATCH_DISK} (${SCRATCH_SIZE_GB} GB) -> /mnt/scratch"
else
  echo "   scratch: none"
fi

# --- Pre-flight: nothing may already exist ----------------------------------
exists() {
  gcloud compute "$1" describe "$2" --project="${PROJECT}" --zone="${ZONE}" \
    --format="value(name)" >/dev/null 2>&1
}
exists instances "${VM_NAME}" && die "instance ${VM_NAME} already exists in ${ZONE}"
exists disks "${BOOT_DISK}" && die "disk ${BOOT_DISK} already exists in ${ZONE}"
if (( SCRATCH_SIZE_GB > 0 )) && exists disks "${SCRATCH_DISK}"; then
  die "disk ${SCRATCH_DISK} already exists in ${ZONE}"
fi

SCHED_ARGS=()
[[ "${SPOT}" -eq 1 ]] && SCHED_ARGS=(--provisioning-model=SPOT
                                     --instance-termination-action=STOP)

echo ">> Plan: ${VM_NAME} in ${PROJECT}/${ZONE}, ${MACHINE_DESC}" \
     "$([[ "${SPOT}" -eq 1 ]] && echo '(spot)')"
if [[ "${DRY_RUN}" -eq 1 ]]; then
  echo ">> Dry run: nothing created."
  exit 0
fi

# --- Create -----------------------------------------------------------------
echo ">> Creating boot disk ${BOOT_DISK}..."
gcloud compute disks create "${BOOT_DISK}" \
  --project="${PROJECT}" --zone="${ZONE}" \
  --source-snapshot="${SNAPSHOT}" \
  --size="${BOOT_DISK_SIZE_GB}" --type="${DISK_TYPE}"
DISK_ARGS=(--disk="name=${BOOT_DISK},boot=yes,auto-delete=yes,device-name=${VM_NAME}")

META_ARGS=()
if (( SCRATCH_SIZE_GB > 0 )); then
  echo ">> Creating scratch disk ${SCRATCH_DISK}..."
  gcloud compute disks create "${SCRATCH_DISK}" \
    --project="${PROJECT}" --zone="${ZONE}" \
    --size="${SCRATCH_SIZE_GB}" --type="${DISK_TYPE}"
  DISK_ARGS+=(--disk="name=${SCRATCH_DISK},boot=no,auto-delete=yes,device-name=scratch")

  STARTUP="$(mktemp)"
  trap 'rm -f "${STARTUP}"' EXIT
  cat > "${STARTUP}" <<'EOF'
#!/usr/bin/env bash
# Format (first boot only) and mount the scratch disk at /mnt/scratch.
set -euo pipefail
DEV=/dev/disk/by-id/google-scratch
[ -e "$DEV" ] || exit 0
blkid "$DEV" >/dev/null 2>&1 || mkfs.ext4 -q -F -m 0 "$DEV"
UUID=$(blkid -s UUID -o value "$DEV")
mkdir -p /mnt/scratch
grep -q "$UUID" /etc/fstab || echo "UUID=$UUID /mnt/scratch ext4 defaults,nofail 0 2" >> /etc/fstab
systemctl daemon-reload
mountpoint -q /mnt/scratch || mount /mnt/scratch
chmod 1777 /mnt/scratch
EOF
  META_ARGS=(--metadata-from-file=startup-script="${STARTUP}")
fi

echo ">> Creating instance ${VM_NAME}..."
gcloud compute instances create "${VM_NAME}" \
  --project="${PROJECT}" --zone="${ZONE}" \
  "${MACHINE_ARGS[@]}" \
  "${DISK_ARGS[@]}" \
  ${SCHED_ARGS[@]+"${SCHED_ARGS[@]}"} \
  ${META_ARGS[@]+"${META_ARGS[@]}"} \
  --network-interface=network=default,subnet=default,network-tier=PREMIUM \
  --service-account="${SERVICE_ACCOUNT}" \
  --scopes=cloud-platform

read -r STATUS IP < <(gcloud compute instances describe "${VM_NAME}" \
  --project="${PROJECT}" --zone="${ZONE}" \
  --format="value(status,networkInterfaces[0].accessConfigs[0].natIP)")
echo ">> Done."
echo "RESULT ${VM_NAME} ${STATUS} ${IP}"
