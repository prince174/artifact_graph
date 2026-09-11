#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

usage() { echo "Usage: deploy.sh [--prod|--prod-external-db|--lab] <git-url> [directory]" >&2; exit 2; }
mode=lab
case "${1:-}" in
  --prod) mode=prod; shift ;;
  --prod-external-db) mode=prod-external-db; shift ;;
  --lab) shift ;;
  --*) usage ;;
esac
[[ $# -ge 1 && $# -le 2 ]] || usage
repo_url="$1"
default_dir=/opt/artefact-graph
[[ "$mode" == lab ]] || default_dir=/opt/artefact-graph-prod
deploy_dir="${2:-$default_dir}"
command -v git >/dev/null && command -v docker >/dev/null && docker compose version >/dev/null
python_bin="${PYTHON_BIN:-python3}"
command -v "$python_bin" >/dev/null 2>&1 || python_bin=python
command -v "$python_bin" >/dev/null

if [[ ! -d "$deploy_dir/.git" ]]; then
  git clone -- "$repo_url" "$deploy_dir"
fi
deploy_dir="$(cd "$deploy_dir" && pwd -P)"
[[ -z "$(git -C "$deploy_dir" status --porcelain)" ]] || { echo "Refusing deployment over a dirty tree" >&2; exit 2; }
git -C "$deploy_dir" pull --ff-only
current_ref="$(git -C "$deploy_dir" rev-parse HEAD)"
export GIT_SHA="$current_ref"

compose_file="$deploy_dir/compose.yaml"
env_file="$deploy_dir/.env"
project_name=artefact-graph
services=()
if [[ "$mode" != lab ]]; then
  compose_file="$deploy_dir/compose.prod.yaml"
  [[ "$mode" != prod-external-db ]] || compose_file="$deploy_dir/compose.prod.external-db.yaml"
  env_file="$deploy_dir/.env.prod"
  project_name=artefact-graph-prod
  services=(postgres graph)
  [[ "$mode" != prod-external-db ]] || services=(graph)
fi
if [[ ! -f "$env_file" ]]; then
  cp -- "$env_file.example" "$env_file"
  chmod 600 "$env_file"
  echo "Created $env_file; fill in server URLs, read-only tokens and random passwords, then rerun deployment." >&2
  exit 2
fi
chmod 600 "$env_file"
# Explicit files/project prevent COMPOSE_FILE or COMPOSE_PROJECT_NAME selecting the lab in production.
compose=(docker compose --project-name "$project_name" --env-file "$env_file" -f "$compose_file" --project-directory "$deploy_dir")
if [[ "$mode" != lab ]]; then
  "${compose[@]}" config --format json | "$python_bin" -c '
import json, pathlib, re, sys
from urllib.parse import parse_qs, urlsplit

config = json.load(sys.stdin)
services = config["services"]
mode = sys.argv[1]
expected_services = {"graph"} if mode == "prod-external-db" else {"graph", "postgres"}
if set(services) != expected_services:
    sys.exit("Production contains an unexpected service set")
env = services["graph"]["environment"]
for name in ("BITBUCKET_URL", "TEAMCITY_URL", "TEAMCITY_PUBLIC_URL"):
    url = urlsplit(env[name])
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        sys.exit(f"{name} must be an HTTPS base URL without credentials, query or fragment")
if mode == "prod-external-db":
    database = urlsplit(env.get("DATABASE_URL", ""))
    query = parse_qs(database.query)
    if database.scheme != "postgresql+psycopg" or not database.hostname or not database.username or not database.password or database.path in {"", "/"} or database.fragment:
        sys.exit("DATABASE_URL must be a complete postgresql+psycopg URL for the external database")
    if query.get("sslmode", [""])[-1] not in {"verify-ca", "verify-full"}:
        sys.exit("External DATABASE_URL must require sslmode=verify-ca or sslmode=verify-full")
    database_password = database.password
else:
    database_password = services["postgres"]["environment"]["POSTGRES_PASSWORD"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", database_password):
        sys.exit("POSTGRES_PASSWORD must have 32+ URL-safe characters; use openssl rand -hex 32")
web_password = env["WEB_PASSWORD"]
if len(web_password) < 32 or web_password.lower().startswith("replace") or web_password == database_password:
    sys.exit("WEB_PASSWORD must be a separate random password of at least 32 characters")
ca_path = env.get("TLS_CA_FILE", "")
database_ca_path = query.get("sslrootcert", [""])[-1] if mode == "prod-external-db" else ""
for path_name, configured_path in (("TLS_CA_FILE", ca_path), ("DATABASE_URL sslrootcert", database_ca_path)):
  if configured_path:
    container_path = pathlib.PurePosixPath(configured_path)
    cert_root = pathlib.PurePosixPath("/app/certs")
    if not container_path.is_relative_to(cert_root) or ".." in container_path.parts:
        sys.exit(f"{path_name} must be a file below /app/certs")
    mount = next(v for v in services["graph"]["volumes"] if v["target"] == str(cert_root))
    cert_file = pathlib.Path(mount["source"]) / str(container_path.relative_to(cert_root))
    if not cert_file.is_file():
        sys.exit(f"{path_name} does not exist in TLS_CA_HOST_DIR")
print("Production configuration validated")
' "$mode"
fi

scan_not_before="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if ! "${compose[@]}" up -d --build --force-recreate "${services[@]}"; then
  echo "Deployment failed. Containers and persistent volumes retained for diagnosis." >&2
  exit 1
fi
published_port="$("${compose[@]}" port graph 8080 | tail -1 | awk -F: '{print $NF}')"
[[ -n "$published_port" ]] || { echo "No published graph port found" >&2; exit 1; }
smoke_env_file="$env_file"
if [[ "$mode" == lab ]]; then
  # Preserve the existing lab invocation contract.
  SMOKE_ENV_FILE="$deploy_dir/.env"
  smoke_env_file="$SMOKE_ENV_FILE"
fi
if ! GRAPH_URL="${GRAPH_URL:-http://localhost:$published_port}" SMOKE_ENV_FILE="$smoke_env_file" \
    SMOKE_NOT_BEFORE="$scan_not_before" bash "$deploy_dir/scripts/smoke-linux.sh"; then
  echo "Deployment smoke failed. Inspect logs and upstream connectivity; volumes were preserved." >&2
  echo "Automatic code/schema downgrade is disabled: migrations may already have run. Restore a verified backup for rollback." >&2
  exit 1
fi
echo "Deployment successful ($mode, $current_ref)"
