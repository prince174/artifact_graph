#!/usr/bin/env bash
# Shared explicit stack selection for operator scripts. This file is sourced.
setup_compose_context() {
  mode=lab
  compose_mode_args=0
  case "${1:-}" in
    --prod) mode=prod; compose_mode_args=1 ;;
    --lab) compose_mode_args=1 ;;
    --*) echo "Unknown mode: use --prod or --lab before the backup path" >&2; return 2 ;;
  esac
  root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
  compose_file="$root/compose.yaml"
  env_file="$root/.env"
  project_name=artefact-graph
  if [[ "$mode" == prod ]]; then
    compose_file="$root/compose.prod.yaml"
    env_file="$root/.env.prod"
    project_name=artefact-graph-prod
  fi
  [[ -f "$env_file" ]] || { echo "Missing $env_file for selected $mode stack" >&2; return 2; }
  compose=(docker compose --project-name "$project_name" --env-file "$env_file" -f "$compose_file" --project-directory "$root")
}

verify_backup_checksum() {
  local backup_path="$1" checksum_line expected_hash actual_line actual_hash
  [[ -f "$backup_path" && -f "$backup_path.sha256" ]] || { echo "Backup or checksum file is missing" >&2; return 2; }
  if ! IFS= read -r checksum_line < "$backup_path.sha256" && [[ -z "$checksum_line" ]]; then
    echo "Invalid backup checksum" >&2; return 1
  fi
  # GNU sha256sum prefixes escaped filenames with a backslash. Compare the actual
  # selected dump, never a different filename mentioned in the checksum manifest.
  checksum_line="${checksum_line#\\}"
  expected_hash="${checksum_line:0:64}"
  [[ "$expected_hash" =~ ^[[:xdigit:]]{64}$ ]] || { echo "Invalid backup checksum" >&2; return 1; }
  actual_line="$(sha256sum -- "$backup_path")"
  actual_line="${actual_line#\\}"
  actual_hash="${actual_line:0:64}"
  [[ "${actual_hash,,}" == "${expected_hash,,}" ]] || { echo "Backup checksum mismatch" >&2; return 1; }
}
