#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
base_url="${GRAPH_URL:-http://localhost:${GRAPH_PORT:-8080}}"
base_url="${base_url%/}"
deadline=$((SECONDS + ${SMOKE_TIMEOUT_SECONDS:-180}))
python_bin="${PYTHON_BIN:-python3}"
command -v "$python_bin" >/dev/null 2>&1 || python_bin=python
cookie_jar="$(mktemp)"
probe_body="$(mktemp)"
cleanup() { rm -f -- "$cookie_jar" "$probe_body"; }
trap cleanup EXIT
curl_args=(--connect-timeout 5 --max-time 15)
[[ -z "${SMOKE_CA_FILE:-}" ]] || curl_args+=(--cacert "$SMOKE_CA_FILE")

read_env_value() {
  "$python_bin" - "$1" "$2" <<'PY'
import ast
import re
import sys

path, wanted = sys.argv[1:]
with open(path, encoding="utf-8") as stream:
    for source_line in stream:
        line = source_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if not separator or key.strip() != wanted:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            try:
                value = ast.literal_eval(value)
            except (SyntaxError, ValueError):
                value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        print(value, end="")
        break
PY
}

until curl "${curl_args[@]}" -fsS "$base_url/health/ready" >/dev/null; do
  (( SECONDS < deadline )) || { echo "readiness timeout" >&2; exit 1; }
  sleep 2
done

probe_status="$(curl "${curl_args[@]}" -sS -o "$probe_body" -w '%{http_code}' "$base_url/api/version")"
if [[ "$probe_status" == "401" ]]; then
  web_username="${WEB_USERNAME:-}"
  web_password="${WEB_PASSWORD:-}"
  if [[ -n "${SMOKE_ENV_FILE:-}" && -r "$SMOKE_ENV_FILE" ]]; then
    [[ -n "$web_username" ]] || web_username="$(read_env_value "$SMOKE_ENV_FILE" WEB_USERNAME)"
    [[ -n "$web_password" ]] || web_password="$(read_env_value "$SMOKE_ENV_FILE" WEB_PASSWORD)"
  fi
  if [[ -z "$web_username" || -z "$web_password" ]]; then
    echo "web authentication is enabled; set WEB_USERNAME and WEB_PASSWORD" >&2
    exit 1
  fi
  export WEB_USERNAME="$web_username" WEB_PASSWORD="$web_password"
  login_status="$(
    "$python_bin" -c 'import os,sys,urllib.parse; sys.stdout.write(urllib.parse.urlencode({"username": os.environ["WEB_USERNAME"], "password": os.environ["WEB_PASSWORD"]}))' |
      curl "${curl_args[@]}" -sS -o /dev/null -w '%{http_code}' -c "$cookie_jar" \
        -H 'Content-Type: application/x-www-form-urlencoded' --data-binary @- "$base_url/login"
  )"
  unset WEB_PASSWORD web_password
  if [[ "$login_status" != "302" && "$login_status" != "303" ]]; then
    echo "web login failed with HTTP $login_status" >&2
    exit 1
  fi
elif [[ "$probe_status" != "200" ]]; then
  echo "version probe failed with HTTP $probe_status" >&2
  exit 1
fi

curl "${curl_args[@]}" -fsS -b "$cookie_jar" "$base_url/api/version"
export SMOKE_NOT_BEFORE="${SMOKE_NOT_BEFORE:-}" SMOKE_MAX_SCAN_AGE_SECONDS="${SMOKE_MAX_SCAN_AGE_SECONDS:-}"
until curl "${curl_args[@]}" -fsS -b "$cookie_jar" "$base_url/api/status" -o "$probe_body" && \
  "$python_bin" - "$probe_body" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Missing scan timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed

with open(sys.argv[1], encoding="utf-8") as stream:
    status = json.load(stream)
scan = status.get("lastScan") or {}
if scan.get("status") != "success":
    sys.exit("Waiting for a successful scan; missing, running, failed or degraded scans are not accepted")
try:
    finished = timestamp(scan["finishedAt"])
    age = (datetime.now(timezone.utc) - finished).total_seconds()
    max_age = int(os.environ["SMOKE_MAX_SCAN_AGE_SECONDS"] or max(int(status.get("refreshMinutes", 60)) * 120, 300))
    not_before = os.environ["SMOKE_NOT_BEFORE"]
    if age < -60 or age > max_age or (not_before and finished < timestamp(not_before)):
        sys.exit("Waiting for a fresh successful scan")
except (KeyError, TypeError, ValueError):
    sys.exit("Scan freshness cannot be verified")
PY
do
  (( SECONDS < deadline )) || { echo "successful scan timeout" >&2; exit 1; }
  sleep 2
done
curl "${curl_args[@]}" -fsS -b "$cookie_jar" "$base_url/api/graph" | "$python_bin" -c '
import json, os, sys
graph = json.load(sys.stdin)
if not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
    sys.exit("Invalid graph response")
if len(graph["nodes"]) < int(os.environ.get("SMOKE_MIN_NODES", "1")):
    sys.exit("Graph is empty; check read permissions or set SMOKE_MIN_NODES=0 for an intentionally empty server")
if any(node.get("stale") for node in graph["nodes"]):
    sys.exit("Graph still contains stale nodes")
print("Graph and fresh successful scan verified")
'
curl "${curl_args[@]}" -fsS "$base_url/metrics" | grep '^artifact_graph_nodes ' >/dev/null
echo "smoke successful"
