#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

checkpoint="outputs/siamese_resnet18_coverage_finetune/best.pt"
calibration_dir="outputs/calibration_final"
prediction_dir="outputs/final_prediction"

[[ -f "$checkpoint" ]] || { echo "missing checkpoint: $checkpoint" >&2; exit 1; }
[[ ! -e "$calibration_dir" ]] || { echo "refusing to overwrite $calibration_dir" >&2; exit 1; }
[[ ! -e "$prediction_dir" ]] || { echo "refusing to overwrite $prediction_dir" >&2; exit 1; }

.venv/bin/python -m hydrowatch.predict \
  --root data/raw/hydrowatch_amur \
  --checkpoint "$checkpoint" \
  --output-dir "$calibration_dir" \
  --pair flood_2021_08_zeya__svobodny \
  --save-probabilities \
  --patch-size 384 \
  --stride 256 \
  --batch-size 4 \
  --device cuda

.venv/bin/python -m hydrowatch.calibrate \
  --root data/raw/hydrowatch_amur \
  --probability-dir "$calibration_dir/probabilities" \
  --pair flood_2021_08_zeya__svobodny \
  --output "$calibration_dir/thresholds.json"

read -r flood_threshold pre_threshold peak_threshold < <(
  .venv/bin/python - <<'PY'
import json
from pathlib import Path

print(*json.loads(Path("outputs/calibration_final/thresholds.json").read_text())["thresholds"])
PY
)

.venv/bin/python -m hydrowatch.predict \
  --root data/raw/hydrowatch_amur \
  --checkpoint "$checkpoint" \
  --output-dir "$prediction_dir" \
  --thresholds "$flood_threshold" "$pre_threshold" "$peak_threshold" \
  --patch-size 384 \
  --stride 256 \
  --batch-size 4 \
  --device cuda

cp "$calibration_dir/thresholds.json" "$prediction_dir/thresholds.json"

.venv/bin/python -m hydrowatch.submission \
  --root data/raw/hydrowatch_amur \
  --submission "$prediction_dir/submission.csv" \
  --mask-dir "$prediction_dir/masks"
