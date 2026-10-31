# Shared by the vm-tracker scripts (sourced, not run).
#
# gcloud's long "wait for operation" call is sometimes dropped by a proxy
# (ProxyError) although the operation proceeds. So every mutating command is
# written `cmd || echo ...` and followed by `poll`, which checks the end state
# with short describe calls.

PROJECT="vino9-276317"
ZONE="us-west1-b"
VM="${VM:-tracker}"          # override only to test with a throwaway VM
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GC=(--project="${PROJECT}" --zone="${ZONE}")

die() { echo "ERROR: $*" >&2; exit 1; }

poll() {  # poll <tries> <command...>   (5 s apart)
  local n="$1" i; shift
  for ((i = 0; i < n; i++)); do "$@" && return 0; sleep 5; done
  return 1
}

# status of a zonal resource (instances|disks); empty when it does not exist
status_of() { gcloud compute "$1" describe "$2" "${GC[@]}" --format='value(status)' 2>/dev/null || true; }
vm_status() { status_of instances "${VM}"; }
has_status() { [[ "$(status_of "$1" "$2")" == "$3" ]]; }
gone() { [[ -z "$(status_of "$1" "$2")" ]]; }          # gone instances|disks NAME
snapshot_ready() {
  [[ "$(gcloud compute snapshots describe "$1" --project="${PROJECT}" \
    --format='value(status)' 2>/dev/null || true)" == "READY" ]]
}

# name of the attached boot disk, or of the disk attached as device name "scratch"
attached_disk() {  # attached_disk boot|scratch
  gcloud compute instances describe "${VM}" "${GC[@]}" --format=json 2>/dev/null \
    | python3 -I -c 'import json, sys
want = sys.argv[1]
for d in json.load(sys.stdin).get("disks", []):
    if (want == "boot" and d.get("boot")) or d.get("deviceName") == want:
        print(d["source"].rsplit("/", 1)[1])' "$1" || true
}

scratch_is() { [[ "$(attached_disk scratch)" == "$1" ]]; }  # scratch_is NAME
scratch_none() { [[ -z "$(attached_disk scratch)" ]]; }

# Point the ~/.ssh/config alias (named like the VM) at the VM's current public
# IP (new block cloned from `station` if missing), drop stale host keys, and wait
# until ssh works. Needed after every create and every start: the IP changes.
refresh_ssh() {
  local ip old i
  ip="$(gcloud compute instances describe "${VM}" "${GC[@]}" \
    --format='value(networkInterfaces[0].accessConfigs[0].natIP)')"
  [[ -n "${ip}" ]] || die "${VM} has no public IP"
  old="$(ssh -G "${VM}" 2>/dev/null | awk '$1 == "hostname" {print $2}')"
  python3 -I "${HERE}/update_ssh_config.py" "${VM}" "${ip}" --template station
  for h in "${VM}" "${ip}" "${old}"; do
    [[ -n "${h}" ]] && ssh-keygen -R "${h}" >/dev/null 2>&1 || true
  done
  echo ">> Waiting for ssh to ${VM} (${ip})..."
  for ((i = 0; i < 36; i++)); do
    ssh -o BatchMode=yes -o ConnectTimeout=8 "${VM}" true 2>/dev/null && return 0
    sleep 5
  done
  die "ssh ${VM} (${ip}) did not come up"
}
