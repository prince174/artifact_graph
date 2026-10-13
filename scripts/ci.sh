#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."

# Run on a dedicated Linux build agent with the tools described in docs/ci.md.
case "${1:-test}" in
  test)
    export DATABASE_URL=sqlite://
    export WEB_PASSWORD=ci-only-password-not-for-production
    python -m pytest -q
    python -m pip_audit --local --progress-spinner off
    docker compose --env-file .env.example config --quiet
    export POSTGRES_PASSWORD=ci-database-password-only-0000000000000000
    export WEB_PASSWORD=ci-web-password-only-11111111111111111111111
    export BITBUCKET_TOKEN=ci-read-only-placeholder TEAMCITY_TOKEN=ci-read-only-placeholder
    docker compose --env-file .env.prod.example -f compose.prod.yaml config --quiet
    DATABASE_URL='postgresql+psycopg://reader:dummy@postgres.example/artifact_graph?sslmode=verify-full' \
      docker compose --env-file .env.prod.example -f compose.prod.external-db.yaml config --quiet
    docker build --build-arg "GIT_SHA=$(git rev-parse HEAD)" -t "artifact-graph:$(git rev-parse HEAD)" .
    ;;
  browser)
    mkdir -p test-results
    cleanup() {
      docker compose -f compose.e2e.yaml logs --no-color > test-results/compose.log 2>&1 || true
      docker compose -f compose.e2e.yaml down --volumes --remove-orphans
    }
    trap cleanup EXIT
    docker compose -f compose.e2e.yaml config --quiet
    docker compose -f compose.e2e.yaml up --build --abort-on-container-exit --exit-code-from browser-e2e
    ;;
  kubernetes)
    helm lint charts/artifact-graph -f charts/artifact-graph/values-local.yaml
    python -m pytest -o addopts='' -q tests/test_helm.py
    python -u scripts/k8s-local.py
    ;;
  security)
    bash scripts/security-scan.sh
    ;;
  all)
    for stage in test browser kubernetes security; do
      bash scripts/ci.sh "$stage"
    done
    ;;
  *)
    echo 'Usage: bash scripts/ci.sh [test|browser|kubernetes|security|all]' >&2
    exit 2
    ;;
esac
