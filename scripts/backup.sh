#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
source "$(dirname "${BASH_SOURCE[0]}")/compose-context.sh"
setup_compose_context "${1:-}"
shift "$compose_mode_args"
[[ $# -le 1 && "${1:-}" != --* ]] || { echo "Usage: backup.sh [--prod|--lab] [backup.dump]" >&2; exit 2; }
backup_dir="$root/backups"
[[ "$mode" != prod ]] || backup_dir="$backup_dir/prod"
output="${1:-$backup_dir/artifact-graph-$(date -u +%Y%m%dT%H%M%SZ).dump}"
mkdir -p -- "$(dirname "$output")"
output="$(cd "$(dirname "$output")" && pwd -P)/$(basename "$output")"
[[ ! -e "$output" && ! -e "$output.sha256" && ! -e "$output.json" ]] || { echo "Refusing to overwrite an existing backup bundle" >&2; exit 2; }
python_bin="${PYTHON_BIN:-python3}"
command -v "$python_bin" >/dev/null 2>&1 || python_bin=python
command -v "$python_bin" >/dev/null
version="$(git -C "$root" describe --always --dirty)"
if ! (set -o noclobber; "${compose[@]}" exec -T postgres pg_dump -U graph -Fc graph > "$output"); then
  echo "Backup creation failed; an incomplete dump may remain at $output (no checksum or manifest created)" >&2
  exit 1
fi
(cd "$(dirname "$output")" && sha256sum -- "$(basename "$output")") > "$output.sha256"
"$python_bin" - "$version" "$(basename "$output")" "$mode" "$project_name" <<'PY' > "$output.json"
import json
import sys
from datetime import datetime, timezone

version, filename, mode, project = sys.argv[1:]
print(json.dumps({"version": version, "createdAt": datetime.now(timezone.utc).isoformat(), "file": filename, "mode": mode, "project": project}))
PY
echo "backup=$output"
