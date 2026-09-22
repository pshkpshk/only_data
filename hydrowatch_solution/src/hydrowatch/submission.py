from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import numpy as np
import rasterio

from hydrowatch.data import load_pairs

SUBMISSION_FIELDS = ("pair_id", "flood_ha", "water_pre_ha", "water_peak_ha")


def validate_submission(root: Path, submission: Path, mask_dir: Path) -> list[str]:
    errors: list[str] = []
    records = load_pairs(root)
    expected = {record.pair_id: record for record in records}
    with submission.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != SUBMISSION_FIELDS:
            errors.append(
                f"submission fields must be {SUBMISSION_FIELDS}, got {reader.fieldnames}"
            )
        rows = list(reader)
    ids = [row.get("pair_id", "") for row in rows]
    duplicates = sorted({pair_id for pair_id in ids if ids.count(pair_id) > 1})
    if duplicates:
        errors.append(f"duplicate pair ids: {duplicates}")
    missing = sorted(set(expected) - set(ids))
    unexpected = sorted(set(ids) - set(expected))
    if missing:
        errors.append(f"missing pair ids: {missing}")
    if unexpected:
        errors.append(f"unexpected pair ids: {unexpected}")

    for row in rows:
        pair_id = row.get("pair_id", "")
        if pair_id not in expected:
            continue
        try:
            areas = [float(row[field]) for field in SUBMISSION_FIELDS[1:]]
        except (KeyError, TypeError, ValueError):
            errors.append(f"{pair_id}: areas must be numeric")
            continue
        if any(not math.isfinite(value) or value < 0.0 for value in areas):
            errors.append(f"{pair_id}: areas must be finite and non-negative")
        if areas[0] > areas[2] + 1e-9:
            errors.append(f"{pair_id}: flood_ha exceeds water_peak_ha")

        mask_path = mask_dir / f"{pair_id}_flood.tif"
        if not mask_path.is_file():
            errors.append(f"{pair_id}: missing flood mask {mask_path}")
            continue
        with rasterio.open(expected[pair_id].reference_mask) as reference, rasterio.open(
            mask_path
        ) as mask:
            same_grid = (
                mask.shape == reference.shape
                and mask.crs == reference.crs
                and mask.transform.almost_equals(reference.transform)
            )
            if not same_grid:
                errors.append(f"{pair_id}: flood mask grid differs from reference")
            if mask.count != 1 or mask.dtypes[0] != "uint8":
                errors.append(f"{pair_id}: flood mask must be one-band uint8")
            values = mask.read(1)
            unique = set(int(value) for value in np.unique(values))
            if not unique.issubset({0, 1}):
                errors.append(f"{pair_id}: flood mask is not binary: {sorted(unique)}")
            pixel_area_ha = abs(mask.transform.a * mask.transform.e) / 10_000.0
            measured_area = float(np.count_nonzero(values == 1) * pixel_area_ha)
            if not np.isclose(measured_area, areas[0], atol=0.011):
                errors.append(
                    f"{pair_id}: flood area {areas[0]:.2f} does not match "
                    f"mask area {measured_area:.2f}"
                )
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate HydroWatch submission and masks")
    parser.add_argument("--root", type=Path, default=Path("data/raw/hydrowatch_amur"))
    parser.add_argument("--submission", type=Path, required=True)
    parser.add_argument("--mask-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    errors = validate_submission(
        args.root.expanduser().resolve(),
        args.submission.expanduser().resolve(),
        args.mask_dir.expanduser().resolve(),
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        raise SystemExit(1)
    print("submission validation: OK")


if __name__ == "__main__":
    main()
