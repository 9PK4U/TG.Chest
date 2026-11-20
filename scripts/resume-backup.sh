#!/usr/bin/env bash
# Also used by systemd ExecStopPost after timeout/cancellation of a backup.
set -euo pipefail
cd "$(dirname "$0")/.."
marker="$(pwd)/backups/.paused-by-backup"
if [[ ! -f "$marker" ]]; then exit 0; fi
while IFS= read -r identifier; do
    [[ "$identifier" =~ ^[a-f0-9]{64}$ ]] || { echo 'Invalid pause recovery record' >&2; exit 1; }
    if ! docker inspect "$identifier" >/dev/null 2>&1; then continue; fi
    project="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$identifier")"
    [[ "$project" == 'tg-chest' ]] || { echo 'Unexpected project in pause recovery record' >&2; exit 1; }
    if [[ "$(docker inspect --format '{{.State.Paused}}' "$identifier")" == 'true' ]]; then
        docker unpause "$identifier" >/dev/null
    fi
done < "$marker"
rm -f -- "$marker"
