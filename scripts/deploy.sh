#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
repo_url="${1:?Usage: deploy.sh <git-url> [directory]}"
deploy_dir="${2:-/opt/artefact-graph}"
if [[ ! -d "$deploy_dir/.git" ]]; then
  git clone "$repo_url" "$deploy_dir"
fi
command -v git >/dev/null && command -v docker >/dev/null && docker compose version >/dev/null
[[ -z "$(git -C "$deploy_dir" status --porcelain)" ]] || { echo "Refusing deployment over a dirty tree" >&2; exit 2; }
previous_ref="$(git -C "$deploy_dir" rev-parse HEAD)"
git -C "$deploy_dir" pull --ff-only
current_ref="$(git -C "$deploy_dir" rev-parse HEAD)"
if [[ ! -f "$deploy_dir/.env" ]]; then
  cp "$deploy_dir/.env.example" "$deploy_dir/.env"
fi
chmod 600 "$deploy_dir/.env"
export GIT_SHA="$current_ref"
compose=(docker compose -f "$deploy_dir/compose.yaml" --project-directory "$deploy_dir")
if ! "${compose[@]}" up -d --build --force-recreate --remove-orphans; then
  failed=1
else
  published_port="$("${compose[@]}" port graph 8080 | tail -1 | awk -F: '{print $NF}')"
  GRAPH_URL="http://localhost:${published_port:-8080}" SMOKE_ENV_FILE="$deploy_dir/.env" \
    "$deploy_dir/scripts/smoke-linux.sh" || failed=1
fi
if [[ "${failed:-0}" == 1 ]]; then
  echo "Deployment failed; rolling back to $previous_ref" >&2
  git -C "$deploy_dir" checkout --detach "$previous_ref"
  export GIT_SHA="$previous_ref"
  docker compose -f "$deploy_dir/compose.yaml" --project-directory "$deploy_dir" up -d --build --force-recreate --remove-orphans
  exit 1
fi
