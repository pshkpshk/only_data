#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
mkdir -p outputs

.venv/bin/hydrowatch-validate --require-scenes data/raw/hydrowatch_amur \
  > outputs/coverage_finetune_validate.log 2>&1
.venv/bin/pytest > outputs/coverage_finetune_tests.log 2>&1
exec .venv/bin/hydrowatch-train \
  --config configs/train_coverage_finetune.yaml \
  --device cuda
