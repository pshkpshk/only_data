#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
mkdir -p outputs

while tmux -L hydrowatch_trainfix0922 has-session -t train_fix 2>/dev/null; do
  sleep 30
done

checkpoint="outputs/siamese_resnet18_coverage_fix/best.pt"
[[ -f "$checkpoint" ]] || { echo "training finished without $checkpoint" >&2; exit 1; }

[[ ! -e outputs/calibration_coverage_fix ]] || {
  echo "refusing to overwrite outputs/calibration_coverage_fix" >&2
  exit 1
}
[[ ! -e outputs/final_prediction_coverage_fix ]] || {
  echo "refusing to overwrite outputs/final_prediction_coverage_fix" >&2
  exit 1
}

.venv/bin/python -m hydrowatch.predict \
  --root data/raw/hydrowatch_amur \
  --checkpoint "$checkpoint" \
  --output-dir outputs/calibration_coverage_fix \
  --pair flood_2021_08_zeya__svobodny \
  --save-probabilities \
  --patch-size 384 \
  --stride 256 \
  --batch-size 4 \
  --device cuda

.venv/bin/python -m hydrowatch.calibrate \
  --root data/raw/hydrowatch_amur \
  --probability-dir outputs/calibration_coverage_fix/probabilities \
  --pair flood_2021_08_zeya__svobodny \
  --output outputs/calibration_coverage_fix/thresholds.json

read -r flood_threshold pre_threshold peak_threshold < <(
  .venv/bin/python - <<'PY'
import json
from pathlib import Path

values = json.loads(
    Path("outputs/calibration_coverage_fix/thresholds.json").read_text()
)["thresholds"]
print(*values)
PY
)

.venv/bin/python -m hydrowatch.predict \
  --root data/raw/hydrowatch_amur \
  --checkpoint "$checkpoint" \
  --output-dir outputs/final_prediction_coverage_fix \
  --thresholds "$flood_threshold" "$pre_threshold" "$peak_threshold" \
  --patch-size 384 \
  --stride 256 \
  --batch-size 4 \
  --device cuda

cp outputs/calibration_coverage_fix/thresholds.json \
  outputs/final_prediction_coverage_fix/thresholds.json

.venv/bin/python -m hydrowatch.submission \
  --root data/raw/hydrowatch_amur \
  --submission outputs/final_prediction_coverage_fix/submission.csv \
  --mask-dir outputs/final_prediction_coverage_fix/masks

./scripts/package_solution.sh \
  "$checkpoint" \
  outputs/final_prediction_coverage_fix \
  outputs/hydrowatch_solution.tar.gz
