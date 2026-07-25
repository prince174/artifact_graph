#!/usr/bin/env bash
set -Eeuo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backup="${1:?Usage: verify-backup.sh <backup.dump>}"
database="graph_verify_$$"
cleanup(){ docker compose -f "$root/compose.yaml" exec -T postgres dropdb -U graph --if-exists "$database" >/dev/null 2>&1 || true; }
trap cleanup EXIT
sha256sum -c "$backup.sha256"
docker compose -f "$root/compose.yaml" exec -T postgres createdb -U graph "$database"
docker compose -f "$root/compose.yaml" exec -T postgres pg_restore -U graph -d "$database" < "$backup"
docker compose -f "$root/compose.yaml" exec -T postgres psql -U graph -d "$database" -v ON_ERROR_STOP=1 -c "SELECT count(*) AS nodes FROM nodes; SELECT count(*) AS edges FROM edges; SELECT count(*) AS scans FROM scans;"
echo "backup verification successful"
