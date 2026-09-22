#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
mkdir -p outputs
exec .venv/bin/hydrowatch-download \
  --root data/raw/hydrowatch_amur \
  --sensor all \
  "$@"
