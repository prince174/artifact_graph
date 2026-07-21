#!/usr/bin/env bash
set -Eeuo pipefail
repo_url="${1:?Usage: deploy.sh <git-url> [directory]}"
deploy_dir="${2:-/opt/artefact-graph}"
if [[ ! -d "$deploy_dir/.git" ]]; then
  git clone "$repo_url" "$deploy_dir"
fi
git -C "$deploy_dir" pull --ff-only
if [[ ! -f "$deploy_dir/.env" ]]; then
  cp "$deploy_dir/.env.example" "$deploy_dir/.env"
fi
docker compose -f "$deploy_dir/compose.yaml" --project-directory "$deploy_dir" up -d --build --force-recreate --remove-orphans

