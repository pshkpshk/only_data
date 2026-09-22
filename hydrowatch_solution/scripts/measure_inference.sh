#!/usr/bin/env bash
# Измеряет время и пиковую память инференса для README (раздел «Ресурсоёмкость»).
# usage: ./scripts/measure_inference.sh [device] [pair_id]
#   ./scripts/measure_inference.sh cuda flood_2021_08_zeya__svobodny
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
device="${1:-auto}"
pair="${2:-flood_2021_08_zeya__svobodny}"
root="${HYDROWATCH_DATA_ROOT:-data/raw/hydrowatch_amur}"
checkpoint="${HYDROWATCH_CHECKPOINT:-weights/best.pt}"
out="outputs/measure_${device}"
rm -rf "$out"

runner=(python -m hydrowatch.predict --root "$root" --checkpoint "$checkpoint" --output-dir "$out" \
  --thresholds 0.95 0.78 0.95 --patch-size 384 --stride 256 --batch-size 4 --device "$device" --pair "$pair")

if [[ "$(uname -s)" == "Darwin" ]]; then
  # macOS: BSD time, пиковая память в байтах
  /usr/bin/time -l "${runner[@]}" 2> "$out.time.txt" || true
  echo "--- $pair on $device ---"
  grep -E "real|maximum resident set size" "$out.time.txt"
elif command -v /usr/bin/time >/dev/null 2>&1; then
  /usr/bin/time -v "${runner[@]}" 2> "$out.time.txt" || true
  echo "--- $pair on $device ---"
  grep -E "Elapsed \(wall clock\)|Maximum resident set size" "$out.time.txt"
else
  start=$(date +%s)
  "${runner[@]}"
  echo "--- $pair on $device: $(( $(date +%s) - start )) s (пиковая память: установите GNU time) ---"
fi
if [[ "$device" == "cuda" ]] && command -v nvidia-smi >/dev/null 2>&1; then
  echo "VRAM: смотрите nvidia-smi --query-gpu=memory.used --format=csv во время прогона"
fi
