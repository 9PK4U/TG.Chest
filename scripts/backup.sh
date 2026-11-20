#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
umask 077
exec 9>/var/lock/tg-chest-backup.lock
flock -n 9 || { echo 'Another Chest backup is running'; exit 0; }
/bin/bash scripts/resume-backup.sh
compose=(docker compose -f compose.yaml)
if [[ -f compose.gateway.yaml ]] && docker network inspect gateway >/dev/null 2>&1; then
    compose+=(-f compose.gateway.yaml)
fi
name="chest-$(date -u +%Y%m%dT%H%M%SZ)-$(cat /proc/sys/kernel/random/uuid | cut -c1-8)"
base="$(pwd)/backups"
work="$base/$name"
mkdir -p "$base"
python3 scripts/clean_backup_work.py "$base"
mkdir -p "$work/payload"
printf '%s\n' "$name" > "$work/backup-name"
paused=()
resume() {
    if ((${#paused[@]})); then
        /bin/bash scripts/resume-backup.sh
        paused=()
    fi
}
finish() {
    local result=$?
    trap - EXIT
    set +e
    resume
    if ((result)); then echo "Backup failed; previous cloud copies kept. Local snapshot: $work" >&2; fi
    exit "$result"
}
trap finish EXIT
helper() {
    "${compose[@]}" run --rm --no-deps -T --user root -v "$work:/backup" --entrypoint python api -m app.backups "$@" </dev/null
}
helper begin "$name"
# Freeze writers without killing in-flight bot handlers. PostgreSQL stays available.
for service in api bot worker; do
    identifier="$("${compose[@]}" ps -q "$service")"
    if [[ -n "$identifier" ]] && [[ "$(docker inspect --format '{{.State.Running}} {{.State.Paused}}' "$identifier")" == 'true false' ]]; then
        paused+=("$identifier")
        printf '%s\n' "${paused[@]}" > "$base/.paused-by-backup"
        docker pause "$identifier" >/dev/null
    fi
done
"${compose[@]}" exec -T db pg_dump -U chest -d chest -Fc > "$work/payload/database.dump" </dev/null
helper inventory /backup/inventory.json
"${compose[@]}" run --rm --no-deps -T --user root -v "$work:/backup" --entrypoint tar api -C /app/data -czf /backup/payload/data.tar.gz . </dev/null
resume
"${compose[@]}" exec -T db pg_restore --list < "$work/payload/database.dump" >/dev/null
# Only recovery credentials; application source and container images are installed separately.
settings=(.env)
if [[ -f yandex-client.secrets.json ]]; then settings+=(yandex-client.secrets.json); fi
tar -czf "$work/payload/settings.tar.gz" "${settings[@]}"
cp "$work/inventory.json" "$work/payload/inventory.json"
cp deploy/README-RESTORE.md "$work/README-RESTORE.md"
cp scripts/restore.py "$work/restore.py"
cp "$work/README-RESTORE.md" "$work/restore.py" "$work/payload/"
tar -C "$work/payload" -czf "$work/snapshot.tar.gz" .
helper manifest /backup
helper publish /backup
python3 scripts/clean_backup_work.py "$base" --after-success
echo "Backup verified on Yandex Disk: $name"
