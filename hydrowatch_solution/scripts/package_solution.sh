#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 3 ]]; then
  echo "usage: $0 CHECKPOINT PREDICTION_DIR OUTPUT_ARCHIVE" >&2
  exit 2
fi

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
checkpoint="$(realpath "$1")"
prediction_dir="$(realpath "$2")"
output_archive="$(realpath -m "$3")"

[[ -f "$checkpoint" ]] || { echo "missing checkpoint: $checkpoint" >&2; exit 1; }
[[ -f "$prediction_dir/submission.csv" ]] || {
  echo "missing submission: $prediction_dir/submission.csv" >&2
  exit 1
}

staging_root="$(mktemp -d)"
trap 'rm -rf "$staging_root"' EXIT
staging="$staging_root/hydrowatch_solution"
mkdir -p "$staging/weights" "$staging/predictions"

cp "$project_dir/README.md" "$project_dir/REPORT.md" "$project_dir/PRESENTATION.md" \
  "$project_dir/pyproject.toml" "$project_dir/uv.lock" "$project_dir/requirements-service.txt" \
  "$project_dir/Dockerfile" "$project_dir/docker-compose.yml" "$project_dir/.dockerignore" "$staging/"
cp -R "$project_dir/src" "$project_dir/configs" "$project_dir/scripts" "$project_dir/tests" \
  "$project_dir/web" "$staging/"
cp "$checkpoint" "$staging/weights/best.pt"
cp "$project_dir/weights/resnet18_sentinel2_all_moco.pth" "$staging/weights/"
cp -R "$prediction_dir/masks" "$staging/predictions/"
cp "$prediction_dir/submission.csv" "$prediction_dir/prediction_metadata.json" \
  "$staging/predictions/"
# pairs.csv рядом с масками — чтобы сервис (docker compose up) работал без монтирования набора
data_root="${HYDROWATCH_DATA_ROOT:-$project_dir/data/raw/hydrowatch_amur}"
[[ -f "$data_root/pairs.csv" ]] && cp "$data_root/pairs.csv" "$staging/predictions/pairs.csv"
if [[ -f "$prediction_dir/thresholds.json" ]]; then
  cp "$prediction_dir/thresholds.json" "$staging/predictions/"
fi
find "$staging" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$staging" -type f -name '*.pyc' -delete

(
  cd "$staging"
  find . -type f ! -name SHA256SUMS.txt -print0 \
    | sort -z \
    | xargs -0 sha256sum > SHA256SUMS.txt
)
mkdir -p "$(dirname "$output_archive")"
COPYFILE_DISABLE=1 tar -C "$staging_root" -czf "$output_archive" hydrowatch_solution
sha256sum "$output_archive" > "$output_archive.sha256"
echo "$output_archive"
