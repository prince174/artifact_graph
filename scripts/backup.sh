#!/usr/bin/env bash
set -Eeuo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output="${1:-$root/backups/artifact-graph-$(date -u +%Y%m%dT%H%M%SZ).dump}"
mkdir -p "$(dirname "$output")"
docker compose -f "$root/compose.yaml" exec -T postgres pg_dump -U graph -Fc graph > "$output"
sha256sum "$output" > "$output.sha256"
printf '{"version":"%s","createdAt":"%s","file":"%s"}\n' "$(git -C "$root" describe --always --dirty)" "$(date -u +%FT%TZ)" "$(basename "$output")" > "$output.json"
echo "backup=$output"
