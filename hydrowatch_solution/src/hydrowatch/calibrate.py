from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import rasterio

from hydrowatch.data import load_pairs

BAND_NAMES = ("flood", "water_pre", "water_peak")
AREA_FLOORS_HA = (50.0, 200.0, 200.0)


def threshold_statistics(
    probability: np.ndarray,
    target: np.ndarray,
    thresholds: np.ndarray,
    *,
    pixel_area_ha: float,
    area_floor_ha: float,
    histogram_bins: int = 10_000,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    probability = np.nan_to_num(probability, nan=0.0, posinf=1.0, neginf=0.0)
    probability = np.clip(probability, 0.0, 1.0)
    positive = target.astype(bool)
    positive_histogram, _ = np.histogram(
        probability[positive], bins=histogram_bins, range=(0.0, 1.0)
    )
    negative_histogram, _ = np.histogram(
        probability[~positive], bins=histogram_bins, range=(0.0, 1.0)
    )
    true_positive = np.cumsum(positive_histogram[::-1])[::-1]
    false_positive = np.cumsum(negative_histogram[::-1])[::-1]
    indices = np.minimum((thresholds * histogram_bins).astype(int), histogram_bins - 1)
    tp = true_positive[indices].astype(np.float64)
    fp = false_positive[indices].astype(np.float64)
    target_count = float(positive.sum())
    false_negative = target_count - tp
    dice = 2.0 * tp / np.maximum(2.0 * tp + fp + false_negative, 1.0)
    predicted_area = (tp + fp) * pixel_area_ha
    target_area = target_count * pixel_area_ha
    area_agreement = np.maximum(
        0.0,
        1.0 - np.abs(predicted_area - target_area) / max(target_area, area_floor_ha),
    )
    combined = 0.7 * dice + 0.3 * area_agreement
    return combined, dice, area_agreement


def calibrate(
    root: Path,
    probability_dir: Path,
    pair_ids: set[str],
    *,
    minimum: float = 0.05,
    maximum: float = 0.95,
    step: float = 0.01,
) -> dict[str, object]:
    records = load_pairs(root)
    if pair_ids:
        records = [record for record in records if record.pair_id in pair_ids]
    if not records:
        raise ValueError("no calibration pairs selected")
    thresholds = np.round(np.arange(minimum, maximum + step / 2.0, step), 6)
    scores = np.zeros((3, len(records), len(thresholds)), dtype=np.float64)
    dice = np.zeros_like(scores)
    area = np.zeros_like(scores)
    for record_index, record in enumerate(records):
        probability_path = probability_dir / f"{record.pair_id}.tif"
        if not probability_path.is_file():
            raise FileNotFoundError(probability_path)
        with rasterio.open(probability_path) as probability_dataset, rasterio.open(
            record.reference_mask
        ) as reference:
            pixel_area_ha = abs(reference.transform.a * reference.transform.e) / 10_000.0
            for band in range(3):
                combined, band_dice, band_area = threshold_statistics(
                    probability_dataset.read(band + 1),
                    reference.read(band + 1),
                    thresholds,
                    pixel_area_ha=pixel_area_ha,
                    area_floor_ha=AREA_FLOORS_HA[band],
                )
                scores[band, record_index] = combined
                dice[band, record_index] = band_dice
                area[band, record_index] = band_area

    result: dict[str, object] = {"pairs": [record.pair_id for record in records], "bands": {}}
    selected: list[float] = []
    bands = result["bands"]
    assert isinstance(bands, dict)
    for band, name in enumerate(BAND_NAMES):
        mean_score = scores[band].mean(axis=0)
        best_index = int(np.argmax(mean_score))
        selected.append(float(thresholds[best_index]))
        bands[name] = {
            "threshold": selected[-1],
            "combined": float(mean_score[best_index]),
            "dice": float(dice[band, :, best_index].mean()),
            "area_agreement": float(area[band, :, best_index].mean()),
        }
    result["thresholds"] = selected
    result["mean_combined"] = float(
        np.mean([bands[name]["combined"] for name in BAND_NAMES])
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calibrate probability thresholds on held-out pairs"
    )
    parser.add_argument("--root", type=Path, default=Path("data/raw/hydrowatch_amur"))
    parser.add_argument("--probability-dir", type=Path, required=True)
    parser.add_argument("--pair", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = calibrate(
        args.root.expanduser().resolve(),
        args.probability_dir.expanduser().resolve(),
        set(args.pair),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
