from __future__ import annotations

import argparse
import csv
import json
from contextlib import nullcontext
from pathlib import Path

import numpy as np
import rasterio
import torch
from rasterio.enums import Resampling
from rasterio.windows import Window
from tqdm import tqdm

from hydrowatch.data import (
    PairRecord,
    _read_bands,
    load_pairs,
    normalize_aux,
    normalize_optical,
    normalize_sar,
)
from hydrowatch.model import SiameseHydroUNet
from hydrowatch.train import select_device


def sliding_positions(length: int, patch_size: int, stride: int) -> list[int]:
    if patch_size <= 0 or stride <= 0 or stride > patch_size:
        raise ValueError("require 0 < stride <= patch_size")
    if length < patch_size:
        raise ValueError(f"raster side {length} is smaller than patch size {patch_size}")
    positions = list(range(0, length - patch_size + 1, stride))
    if positions[-1] != length - patch_size:
        positions.append(length - patch_size)
    return positions


def blend_weights(patch_size: int) -> np.ndarray:
    axis = np.hanning(patch_size).astype(np.float32)
    weight = np.outer(axis, axis)
    return np.clip(weight, 0.05, None)[None]


def _read_full_inputs(record: PairRecord) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    with rasterio.open(record.reference_mask) as reference:
        window = Window(0, 0, reference.width, reference.height)
        height, width = reference.height, reference.width

    s1_paths = (record.scene("S1_pre"), record.scene("S1_peak"))
    if any(path is None for path in s1_paths):
        raise FileNotFoundError(f"{record.pair_id}: missing required Sentinel-1 scene")
    sar = np.stack(
        [normalize_sar(_read_bands(path, (1, 2), window)) for path in s1_paths if path],
        axis=0,
    )

    optical_arrays: list[np.ndarray] = []
    optical_available: list[float] = []
    for stem in ("SENTINEL2_pre", "SENTINEL2_peak"):
        path = record.scene(stem)
        if path is None:
            optical_arrays.append(np.zeros((4, height, width), dtype=np.float32))
            optical_available.append(0.0)
        else:
            optical_arrays.append(normalize_optical(_read_bands(path, (1, 2, 3, 4), window)))
            optical_available.append(1.0)
    optical = np.stack(optical_arrays, axis=0)
    aux = normalize_aux(
        _read_bands(
            record.aux,
            (1, 2, 3, 4, 5, 6),
            window,
            align_to=record.reference_mask,
            resampling=Resampling.bilinear,
        )
    )
    return sar, optical, aux, np.asarray(optical_available, dtype=np.float32)


def load_model(checkpoint: Path, device: torch.device) -> SiameseHydroUNet:
    payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
    model_config = payload["config"]["model"]
    model = SiameseHydroUNet(
        sar_channels=int(model_config["sar_channels"]),
        optical_channels=int(model_config["optical_channels"]),
        aux_channels=int(model_config["aux_channels"]),
    )
    model.load_state_dict(payload["model"], strict=True)
    model.to(device).eval()
    return model


def predict_probabilities(
    model: SiameseHydroUNet,
    record: PairRecord,
    device: torch.device,
    *,
    patch_size: int,
    stride: int,
    batch_size: int,
    amp: bool,
    ablation: str = "none",
) -> np.ndarray:
    """Sliding-window probabilities.

    ``ablation`` switches off an input branch at inference time (for the report):
    ``"no_optical"`` feeds zeros with availability flags = 0 (the modality-dropout mode
    the network was trained with); ``"no_aux"`` zeroes the terrain/GSW stack.
    """
    sar, optical, aux, optical_available = _read_full_inputs(record)
    if ablation == "no_optical":
        optical = np.zeros_like(optical)
        optical_available = np.zeros_like(optical_available)
    elif ablation == "no_aux":
        aux = np.zeros_like(aux)
    elif ablation != "none":
        raise ValueError(f"unknown ablation {ablation!r}")
    height, width = sar.shape[-2:]
    coordinates = [
        (row, col)
        for row in sliding_positions(height, patch_size, stride)
        for col in sliding_positions(width, patch_size, stride)
    ]
    weight = blend_weights(patch_size)
    total = np.zeros((3, height, width), dtype=np.float32)
    denominator = np.zeros((1, height, width), dtype=np.float32)
    with torch.inference_mode():
        for offset in tqdm(
            range(0, len(coordinates), batch_size),
            desc=record.pair_id,
            leave=False,
        ):
            batch_coordinates = coordinates[offset : offset + batch_size]
            sar_batch = np.stack(
                [
                    sar[:, :, row : row + patch_size, col : col + patch_size]
                    for row, col in batch_coordinates
                ]
            )
            optical_batch = np.stack(
                [
                    optical[:, :, row : row + patch_size, col : col + patch_size]
                    for row, col in batch_coordinates
                ]
            )
            aux_batch = np.stack(
                [
                    aux[:, row : row + patch_size, col : col + patch_size]
                    for row, col in batch_coordinates
                ]
            )
            availability_batch = np.repeat(
                optical_available[None], len(batch_coordinates), axis=0
            )
            autocast = (
                torch.autocast(device_type="cuda", dtype=torch.float16)
                if amp and device.type == "cuda"
                else nullcontext()
            )
            with autocast:
                logits = model(
                    torch.from_numpy(sar_batch).to(device),
                    torch.from_numpy(optical_batch).to(device),
                    torch.from_numpy(aux_batch).to(device),
                    torch.from_numpy(availability_batch).to(device),
                )
                probabilities = torch.sigmoid(logits).float().cpu().numpy()
            for probability, (row, col) in zip(
                probabilities, batch_coordinates, strict=True
            ):
                total[:, row : row + patch_size, col : col + patch_size] += probability * weight
                denominator[:, row : row + patch_size, col : col + patch_size] += weight
    return total / np.maximum(denominator, 1e-6)


def postprocess_masks(
    probabilities: np.ndarray, thresholds: tuple[float, float, float]
) -> np.ndarray:
    if probabilities.shape[0] != 3:
        raise ValueError(f"expected 3 probability bands, got {probabilities.shape[0]}")
    flood = probabilities[0] >= thresholds[0]
    water_pre = probabilities[1] >= thresholds[1]
    water_peak = probabilities[2] >= thresholds[2]
    water_peak |= flood
    flood &= ~water_pre
    return np.stack((flood, water_pre, water_peak)).astype(np.uint8)


def apply_pair_rules(masks: np.ndarray, event_kind: str) -> np.ndarray:
    """Apply competition-safe rules that depend only on published pair metadata."""
    if masks.shape[0] != 3:
        raise ValueError(f"expected 3 mask bands, got {masks.shape[0]}")
    result = masks.copy()
    if event_kind == "baseline":
        result[0].fill(0)
    return result


def _write_mask(
    path: Path, reference_path: Path, mask: np.ndarray, descriptions: tuple[str, ...]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(reference_path) as reference:
        profile = reference.profile.copy()
    profile.update(
        driver="GTiff",
        count=mask.shape[0],
        dtype="uint8",
        nodata=None,
        compress="deflate",
        predictor=1,
        tiled=True,
        blockxsize=512,
        blockysize=512,
    )
    with rasterio.open(path, "w", **profile) as output:
        output.write(mask)
        output.descriptions = descriptions


def _write_probabilities(path: Path, reference_path: Path, probabilities: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(reference_path) as reference:
        profile = reference.profile.copy()
    profile.update(
        driver="GTiff",
        count=3,
        dtype="float32",
        nodata=None,
        compress="deflate",
        predictor=3,
        tiled=True,
        blockxsize=512,
        blockysize=512,
        BIGTIFF="IF_SAFER",
    )
    with rasterio.open(path, "w", **profile) as output:
        output.write(probabilities.astype(np.float32, copy=False))
        output.descriptions = (
            "flood_probability",
            "water_pre_probability",
            "water_peak_probability",
        )


def _portable_path(path: Path) -> str:
    """Store paths relative to the working directory so metadata is machine independent."""
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return path.name


def predict(
    root: Path,
    checkpoint: Path,
    output_dir: Path,
    *,
    thresholds: tuple[float, float, float],
    patch_size: int,
    stride: int,
    batch_size: int,
    device_name: str,
    pair_ids: set[str],
    save_probabilities: bool,
    ablation: str = "none",
) -> Path:
    device = select_device(device_name)
    model = load_model(checkpoint, device)
    records = load_pairs(root)
    if pair_ids:
        records = [record for record in records if record.pair_id in pair_ids]
        missing = pair_ids - {record.pair_id for record in records}
        if missing:
            raise ValueError(f"unknown pair ids: {sorted(missing)}")
    output_dir.mkdir(parents=True, exist_ok=True)
    mask_dir = output_dir / "masks"
    probability_dir = output_dir / "probabilities"
    rows: list[dict[str, str | float]] = []
    area_details: dict[str, dict[str, float]] = {}
    for record in records:
        probabilities = predict_probabilities(
            model,
            record,
            device,
            patch_size=patch_size,
            stride=stride,
            batch_size=batch_size,
            amp=True,
            ablation=ablation,
        )
        masks = apply_pair_rules(
            postprocess_masks(probabilities, thresholds), record.event_kind
        )
        with rasterio.open(record.reference_mask) as reference:
            pixel_area_ha = abs(reference.transform.a * reference.transform.e) / 10_000.0
        areas = masks.reshape(3, -1).sum(axis=1, dtype=np.float64) * pixel_area_ha
        row = {
            "pair_id": record.pair_id,
            "flood_ha": round(float(areas[0]), 2),
            "water_pre_ha": round(float(areas[1]), 2),
            "water_peak_ha": round(float(areas[2]), 2),
        }
        rows.append(row)
        area_details[record.pair_id] = {
            key: float(value) for key, value in row.items() if key != "pair_id"
        }
        _write_mask(
            mask_dir / f"{record.pair_id}_flood.tif",
            record.reference_mask,
            masks[0:1],
            ("flood",),
        )
        _write_mask(
            mask_dir / f"{record.pair_id}_all.tif",
            record.reference_mask,
            masks,
            ("flood", "water_pre", "water_peak"),
        )
        if save_probabilities:
            _write_probabilities(
                probability_dir / f"{record.pair_id}.tif",
                record.reference_mask,
                probabilities,
            )

    submission = output_dir / "submission.csv"
    with submission.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=("pair_id", "flood_ha", "water_pre_ha", "water_peak_ha")
        )
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / "prediction_metadata.json").write_text(
        json.dumps(
            {
                "checkpoint": _portable_path(checkpoint),
                "thresholds": thresholds,
                "patch_size": patch_size,
                "stride": stride,
                "ablation": ablation,
                "areas": area_details,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return submission


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sliding-window HydroWatch inference")
    parser.add_argument("--root", type=Path, default=Path("data/raw/hydrowatch_amur"))
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--thresholds", type=float, nargs=3, default=(0.5, 0.5, 0.5))
    parser.add_argument("--patch-size", type=int, default=384)
    parser.add_argument("--stride", type=int, default=256)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--pair", action="append", default=[])
    parser.add_argument("--save-probabilities", action="store_true")
    parser.add_argument(
        "--ablation",
        choices=("none", "no_optical", "no_aux"),
        default="none",
        help="inference-time ablation for the report: drop the optical branch or the AUX stack",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    thresholds = tuple(float(value) for value in args.thresholds)
    if any(not 0.0 < value < 1.0 for value in thresholds):
        raise SystemExit("thresholds must be between 0 and 1")
    submission = predict(
        args.root.expanduser().resolve(),
        args.checkpoint.expanduser().resolve(),
        args.output_dir.expanduser().resolve(),
        thresholds=thresholds,
        patch_size=args.patch_size,
        stride=args.stride,
        batch_size=args.batch_size,
        device_name=args.device,
        pair_ids=set(args.pair),
        save_probabilities=args.save_probabilities,
        ablation=args.ablation,
    )
    print(f"submission: {submission}")


if __name__ == "__main__":
    main()
