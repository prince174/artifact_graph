#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
source "$(dirname "${BASH_SOURCE[0]}")/compose-context.sh"
setup_compose_context "${1:-}"
shift "$compose_mode_args"
[[ $# == 2 && "${1:-}" != --* && "${2:-}" == "--confirm" ]] || { echo "Usage: restore.sh [--prod|--lab] <backup.dump> --confirm; refusing destructive restore without --confirm" >&2; exit 2; }
backup="$1"
verify_backup_checksum "$backup"
# Reject an unreadable/corrupt archive before stopping the app or deleting its DB.
"${compose[@]}" exec -T postgres pg_restore --list < "$backup" >/dev/null
echo "Restoring database graph in Compose project $project_name ($mode)"
"${compose[@]}" stop graph
"${compose[@]}" exec -T postgres dropdb -U graph --if-exists graph
"${compose[@]}" exec -T postgres createdb -U graph graph
"${compose[@]}" exec -T postgres pg_restore --exit-on-error -U graph -d graph --clean --if-exists < "$backup"
"${compose[@]}" up -d --no-deps graph
