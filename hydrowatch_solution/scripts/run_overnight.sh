#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
mkdir -p outputs

expected_scenes=34
retry_count=0
while true; do
  scene_count="$(find data/raw/hydrowatch_amur/rasters -type f \( -name 'S1_*.tif' -o -name 'SENTINEL2_*.tif' \) | wc -l)"
  if [[ "$scene_count" -ge "$expected_scenes" ]]; then
    break
  fi
  if ! tmux -L hydrowatch0921 has-session -t download 2>/dev/null; then
    retry_count="$((retry_count + 1))"
    if [[ "$retry_count" -gt 3 ]]; then
      echo "download incomplete after 3 retries: ${scene_count}/${expected_scenes}" >&2
      exit 1
    fi
    echo "retry ${retry_count}: ${scene_count}/${expected_scenes} scenes" \
      >> outputs/download_retry.log
    .venv/bin/hydrowatch-download \
      --root data/raw/hydrowatch_amur \
      --sensor all \
      --manifest outputs/stac_manifest.json \
      >> outputs/download_retry.log 2>&1 || true
  fi
  sleep 30
done

.venv/bin/hydrowatch-validate --require-scenes data/raw/hydrowatch_amur \
  > outputs/validate_strict.log 2>&1
.venv/bin/pytest > outputs/tests.log 2>&1
.venv/bin/hydrowatch-train --config configs/train.yaml --device cuda \
  > outputs/train.log 2>&1
