#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
source "$(dirname "${BASH_SOURCE[0]}")/compose-context.sh"
setup_compose_context "${1:-}"
shift "$compose_mode_args"
[[ $# == 1 && "$1" != --* ]] || { echo "Usage: verify-backup.sh [--prod|--lab] <backup.dump>" >&2; exit 2; }
backup="$1"
verify_backup_checksum "$backup"
database="graph_verify_${RANDOM}_$$"
"${compose[@]}" exec -T postgres createdb -U graph "$database"
cleanup(){ "${compose[@]}" exec -T postgres dropdb -U graph --if-exists "$database" >/dev/null 2>&1 || true; }
# Only drop a verification database after this process successfully created it.
trap cleanup EXIT
"${compose[@]}" exec -T postgres pg_restore --exit-on-error -U graph -d "$database" < "$backup"
"${compose[@]}" exec -T postgres psql -U graph -d "$database" -v ON_ERROR_STOP=1 -c "SELECT count(*) AS nodes FROM nodes; SELECT count(*) AS edges FROM edges; SELECT count(*) AS scans FROM scans;"
echo "backup verification successful"
