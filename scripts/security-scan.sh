#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."

image_ref="${SECURITY_IMAGE_REF:-artifact-graph:security-scan}"
report_dir="${SECURITY_REPORT_DIR:-test-results/security}"
mkdir -p "$report_dir"
docker build --build-arg "GIT_SHA=$(git rev-parse HEAD)" -t "$image_ref" .
# Retain an inventory even when the following gate rejects the image.
trivy image --scanners vuln --severity CRITICAL,HIGH --ignore-unfixed=false \
  --exit-code 0 --format table --output "$report_dir/trivy-all-high-critical.txt" "$image_ref"
trivy image --scanners vuln --severity CRITICAL,HIGH --ignore-unfixed=false \
  --exit-code 1 --format table --output "$report_dir/trivy-fixable-high-critical.txt" "$image_ref"
