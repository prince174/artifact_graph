#!/usr/bin/env bash
set -Eeuo pipefail
base_url="${GRAPH_URL:-http://localhost:${GRAPH_PORT:-8080}}"
deadline=$((SECONDS + ${SMOKE_TIMEOUT_SECONDS:-180}))
python_bin="${PYTHON_BIN:-python3}"
command -v "$python_bin" >/dev/null 2>&1 || python_bin=python
cookie_jar="$(mktemp)"
probe_body="$(mktemp)"
cleanup() { rm -f -- "$cookie_jar" "$probe_body"; }
trap cleanup EXIT

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

until curl -fsS "$base_url/health/ready" >/dev/null; do
  (( SECONDS < deadline )) || { echo "readiness timeout" >&2; exit 1; }
  sleep 2
done

probe_status="$(curl -sS -o "$probe_body" -w '%{http_code}' "$base_url/api/version")"
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
      curl -sS -o /dev/null -w '%{http_code}' -c "$cookie_jar" \
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

curl -fsS -b "$cookie_jar" "$base_url/api/version"
curl -fsS -b "$cookie_jar" "$base_url/api/status"
curl -fsS -b "$cookie_jar" "$base_url/api/graph" | "$python_bin" -c 'import json,sys; g=json.load(sys.stdin); assert g["nodes"] and g["edges"]'
curl -fsS "$base_url/metrics" | grep -q '^artifact_graph_nodes '
echo "smoke successful"
