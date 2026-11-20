#!/usr/bin/env bash
# Apply staged application artifacts; never disables backups or touches DB volumes.
set -Eeuo pipefail
cd /opt/tg-chest
umask 077
exec 9>/var/lock/tg-chest-backup.lock
flock -w 60 9 || { echo 'Backup is running; deployment deferred without changes'; exit 1; }
test -f source.tgz
test -f image.tar
compose=(docker compose -f compose.yaml -f compose.gateway.yaml)
db_container="$("${compose[@]}" ps -q db)"
test "$(docker inspect --format '{{.State.Health.Status}}' "$db_container")" = healthy
api_container="$("${compose[@]}" ps -q api)"
previous="$(docker inspect --format '{{.Image}}' "$api_container")"
docker image tag "$previous" tg-chest:previous
tar -xzf source.tgz
docker load -i image.tar
# Stop old writers before applying the privacy migration. DB, gateway and backup
# timer remain running throughout; Compose starts the new writers after migrate.
"${compose[@]}" stop api bot worker </dev/null
"${compose[@]}" up -d --no-build api bot worker </dev/null
"${compose[@]}" exec -T db psql -U chest -d chest -At -c 'SELECT version_num FROM alembic_version' </dev/null
curl -fsS --retry 8 --retry-delay 2 --retry-all-errors https://chest.pk4u.ru/api/health
echo
"${compose[@]}" ps </dev/null
