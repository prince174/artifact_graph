#!/usr/bin/env bash
set -Eeuo pipefail
base_url="${GRAPH_URL:-http://localhost:${GRAPH_PORT:-8080}}"
deadline=$((SECONDS + ${SMOKE_TIMEOUT_SECONDS:-180}))
until curl -fsS "$base_url/health/ready" >/dev/null; do
  (( SECONDS < deadline )) || { echo "readiness timeout" >&2; exit 1; }
  sleep 2
done
curl -fsS "$base_url/api/version"
curl -fsS "$base_url/api/status"
curl -fsS "$base_url/api/graph" | python -c 'import json,sys; g=json.load(sys.stdin); assert g["nodes"] and g["edges"]'
curl -fsS "$base_url/metrics" | grep -q '^artifact_graph_nodes '
echo "smoke successful"
