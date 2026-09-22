from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rasterio

from hydrowatch.data import AUX_BANDS, MASK_BANDS, PairRecord, load_pairs


@dataclass
class ValidationReport:
    root: Path
    pair_count: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked_pairs: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "ok": self.ok,
            "pair_count": self.pair_count,
            "checked_pairs": self.checked_pairs,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def _same_grid(left: rasterio.DatasetReader, right: rasterio.DatasetReader) -> bool:
    return (
        left.width == right.width
        and left.height == right.height
        and left.crs == right.crs
        and left.transform.almost_equals(right.transform)
    )


def _mask_counts(dataset: rasterio.DatasetReader) -> tuple[list[int], set[int]]:
    counts = [0 for _ in range(dataset.count)]
    values: set[int] = set()
    for _, window in dataset.block_windows(1):
        block = dataset.read(window=window)
        values.update(int(value) for value in np.unique(block))
        for band in range(dataset.count):
            counts[band] += int(np.count_nonzero(block[band] == 1))
    return counts, values


def _validate_scene(
    report: ValidationReport,
    record: PairRecord,
    reference: rasterio.DatasetReader,
    stem: str,
    minimum_bands: int,
    required: bool,
) -> None:
    try:
        path = record.scene(stem)
    except ValueError as error:
        report.errors.append(str(error))
        return
    if path is None:
        message = f"{record.pair_id}: missing {stem} GeoTIFF"
        (report.errors if required else report.warnings).append(message)
        return
    try:
        with rasterio.open(path) as scene:
            if scene.count < minimum_bands:
                report.errors.append(
                    f"{record.pair_id}: {path.name} has {scene.count} bands; need {minimum_bands}"
                )
            if not _same_grid(reference, scene):
                report.errors.append(f"{record.pair_id}: {path.name} is not aligned to reference")
            valid_pixels = 0
            total_pixels = scene.width * scene.height
            for _, window in scene.block_windows(1):
                masks = scene.read_masks(window=window)
                valid_pixels += int(np.count_nonzero(np.all(masks > 0, axis=0)))
            valid_fraction = valid_pixels / total_pixels
            if valid_fraction < 0.95:
                message = (
                    f"{record.pair_id}: {path.name} covers only "
                    f"{valid_fraction:.3%} of the reference grid"
                )
                (report.errors if required else report.warnings).append(message)
            elif valid_fraction < 0.999:
                report.warnings.append(
                    f"{record.pair_id}: {path.name} valid coverage is "
                    f"{valid_fraction:.3%}"
                )
    except rasterio.errors.RasterioIOError as error:
        report.errors.append(f"{record.pair_id}: cannot open {path}: {error}")


def _validate_pair(report: ValidationReport, record: PairRecord, require_scenes: bool) -> None:
    if not record.reference_mask.is_file():
        report.errors.append(f"{record.pair_id}: missing reference mask {record.reference_mask}")
        return
    if not record.aux.is_file():
        report.errors.append(f"{record.pair_id}: missing auxiliary raster {record.aux}")
        return

    try:
        with rasterio.open(record.reference_mask) as reference, rasterio.open(record.aux) as aux:
            if reference.count != len(MASK_BANDS):
                report.errors.append(
                    f"{record.pair_id}: reference has {reference.count} bands; "
                    f"expected {len(MASK_BANDS)}"
                )
            if any(dtype != "uint8" for dtype in reference.dtypes):
                report.errors.append(
                    f"{record.pair_id}: reference dtypes must be uint8, got {reference.dtypes}"
                )
            if reference.crs is None or reference.crs.to_epsg() != 32652:
                report.errors.append(f"{record.pair_id}: expected EPSG:32652, got {reference.crs}")
            resolution = tuple(abs(value) for value in reference.res)
            if not np.allclose(resolution, (10.0, 10.0), atol=1e-6):
                report.errors.append(f"{record.pair_id}: expected 10 m pixels, got {reference.res}")
            if aux.count != len(AUX_BANDS):
                report.errors.append(
                    f"{record.pair_id}: AUX has {aux.count} bands; expected {len(AUX_BANDS)}"
                )
            if not _same_grid(reference, aux):
                if aux.crs != reference.crs:
                    report.errors.append(
                        f"{record.pair_id}: AUX CRS {aux.crs} differs from "
                        f"reference {reference.crs}"
                    )
                else:
                    report.warnings.append(
                        f"{record.pair_id}: AUX grid is {abs(aux.res[0]):g} m, "
                        "it will be resampled to the 10 m reference grid"
                    )

            counts, values = _mask_counts(reference)
            if not values.issubset({0, 1}):
                report.errors.append(
                    f"{record.pair_id}: reference mask contains non-binary values {sorted(values)}"
                )

            if record.reference_metadata.is_file() and len(counts) == len(MASK_BANDS):
                with record.reference_metadata.open(encoding="utf-8") as stream:
                    metadata = json.load(stream)
                pixel_area_ha = abs(reference.transform.a * reference.transform.e) / 10_000.0
                for band_index, name in enumerate(MASK_BANDS):
                    stated = float(metadata["stats"][f"{name}_ha"])
                    measured = counts[band_index] * pixel_area_ha
                    if not np.isclose(stated, measured, atol=0.011):
                        report.warnings.append(
                            f"{record.pair_id}: {name} area mismatch: metadata={stated:.2f} ha, "
                            f"mask={measured:.2f} ha; training will trust the GeoTIFF"
                        )

            for stem in ("S1_pre", "S1_peak"):
                _validate_scene(report, record, reference, stem, 2, require_scenes)
            for stem in ("SENTINEL2_pre", "SENTINEL2_peak"):
                if record.has_optical or record.scene(stem) is not None:
                    _validate_scene(
                        report,
                        record,
                        reference,
                        stem,
                        4,
                        require_scenes and record.has_optical,
                    )
    except (OSError, rasterio.errors.RasterioIOError, ValueError, KeyError) as error:
        report.errors.append(f"{record.pair_id}: validation failed: {error}")
        return

    report.checked_pairs.append(record.pair_id)


def validate_dataset(root: str | Path, *, require_scenes: bool = False) -> ValidationReport:
    resolved_root = Path(root).expanduser().resolve()
    report = ValidationReport(root=resolved_root)
    try:
        records = load_pairs(resolved_root)
    except (FileNotFoundError, ValueError, KeyError) as error:
        report.errors.append(str(error))
        return report
    report.pair_count = len(records)
    for record in records:
        _validate_pair(report, record, require_scenes)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate HydroWatch rasters and metadata")
    parser.add_argument("root", type=Path, help="Path containing pairs.csv")
    parser.add_argument(
        "--require-scenes",
        action="store_true",
        help="Treat missing Sentinel-1/2 GeoTIFFs as errors",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = validate_dataset(args.root, require_scenes=args.require_scenes)
    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        status = "OK" if report.ok else "FAILED"
        print(
            f"dataset validation: {status}; checked {len(report.checked_pairs)}/{report.pair_count}"
        )
        for message in report.errors:
            print(f"ERROR: {message}")
        for message in report.warnings:
            print(f"WARNING: {message}")
    raise SystemExit(0 if report.ok else 1)


if __name__ == "__main__":
    main()
