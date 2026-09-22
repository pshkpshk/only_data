from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import rasterio

from hydrowatch.data import load_pairs
from hydrowatch.submission import SUBMISSION_FIELDS, validate_submission


def test_submission_validator_accepts_matching_binary_mask(
    tiny_dataset: Path, tmp_path: Path
) -> None:
    record = load_pairs(tiny_dataset)[0]
    mask_dir = tmp_path / "masks"
    mask_dir.mkdir()
    mask_path = mask_dir / f"{record.pair_id}_flood.tif"
    with rasterio.open(record.reference_mask) as reference:
        profile = reference.profile.copy()
        flood = reference.read(1)
    profile.update(count=1, dtype="uint8", nodata=None)
    with rasterio.open(mask_path, "w", **profile) as output:
        output.write(flood[None].astype(np.uint8))

    submission = tmp_path / "submission.csv"
    with submission.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=SUBMISSION_FIELDS)
        writer.writeheader()
        writer.writerow(
            {
                "pair_id": record.pair_id,
                "flood_ha": 0.2,
                "water_pre_ha": 0.2,
                "water_peak_ha": 0.4,
            }
        )
    assert validate_submission(tiny_dataset, submission, mask_dir) == []
