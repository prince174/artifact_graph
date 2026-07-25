#!/usr/bin/env bash
set -Eeuo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backup="${1:?Usage: restore.sh <backup.dump> --confirm}"
[[ "${2:-}" == "--confirm" ]] || { echo "Refusing destructive restore without --confirm" >&2; exit 2; }
sha256sum -c "$backup.sha256"
docker compose -f "$root/compose.yaml" stop graph
docker compose -f "$root/compose.yaml" exec -T postgres dropdb -U graph --if-exists graph
docker compose -f "$root/compose.yaml" exec -T postgres createdb -U graph graph
docker compose -f "$root/compose.yaml" exec -T postgres pg_restore -U graph -d graph --clean --if-exists < "$backup"
docker compose -f "$root/compose.yaml" up -d graph
