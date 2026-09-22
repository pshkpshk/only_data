from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
import torch
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window
from torch.utils.data import Dataset

MASK_BANDS = ("flood", "water_pre", "water_peak", "permanent", "receded")
AUX_BANDS = ("slope", "hand", "occurrence", "seasonality", "max_extent", "builtup")


@dataclass(frozen=True)
class PairRecord:
    pair_id: str
    aoi_id: str
    event_id: str
    event_kind: str
    rasters_dir: Path
    reference_mask: Path
    has_optical: bool

    @classmethod
    def from_csv_row(cls, root: Path, row: dict[str, str]) -> PairRecord:
        return cls(
            pair_id=row["pair_id"],
            aoi_id=row["aoi_id"],
            event_id=row["event_id"],
            event_kind=row["event_kind"],
            rasters_dir=root / row["rasters_dir"],
            reference_mask=root / row["reference_mask"],
            has_optical=bool(row.get("sensor_optical", "").strip()),
        )

    @property
    def aux(self) -> Path:
        return self.rasters_dir / "AUX_terrain_gsw.tif"

    @property
    def reference_metadata(self) -> Path:
        return self.reference_mask.with_suffix(".json")

    def scene(self, stem: str) -> Path | None:
        """Resolve one scene GeoTIFF without silently choosing between duplicates."""
        exact = self.rasters_dir / f"{stem}.tif"
        matches = [exact] if exact.exists() else sorted(self.rasters_dir.glob(f"{stem}_*.tif"))
        if len(matches) > 1:
            names = ", ".join(path.name for path in matches)
            raise ValueError(f"{self.pair_id}: multiple rasters match {stem}: {names}")
        return matches[0] if matches else None


def load_pairs(root: str | Path) -> list[PairRecord]:
    root = Path(root).expanduser().resolve()
    pairs_csv = root / "pairs.csv"
    if not pairs_csv.is_file():
        raise FileNotFoundError(f"pairs.csv not found under {root}")

    with pairs_csv.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError(f"{pairs_csv} contains no pairs")

    records = [PairRecord.from_csv_row(root, row) for row in rows]
    ids = [record.pair_id for record in records]
    duplicates = sorted({pair_id for pair_id in ids if ids.count(pair_id) > 1})
    if duplicates:
        raise ValueError(f"duplicate pair_id values: {duplicates}")
    return records


def split_by_event(
    records: Iterable[PairRecord], validation_event: str
) -> tuple[list[PairRecord], list[PairRecord]]:
    train = [record for record in records if record.event_id != validation_event]
    validation = [record for record in records if record.event_id == validation_event]
    if not train or not validation:
        raise ValueError(
            f"event split must produce non-empty sets; validation_event={validation_event!r}"
        )
    return train, validation


def _read_bands(
    path: Path,
    bands: tuple[int, ...],
    window: Window,
    *,
    align_to: Path | None = None,
    resampling: Resampling = Resampling.nearest,
) -> np.ndarray:
    with rasterio.open(path) as dataset:
        if max(bands) > dataset.count:
            raise ValueError(
                f"{path} has {dataset.count} bands, requested 1-based band {max(bands)}"
            )
        if align_to is None:
            array = dataset.read(bands, window=window, out_dtype="float32")
        else:
            with rasterio.open(align_to) as reference:
                aligned = (
                    dataset.width == reference.width
                    and dataset.height == reference.height
                    and dataset.crs == reference.crs
                    and dataset.transform.almost_equals(reference.transform)
                )
                if aligned:
                    array = dataset.read(bands, window=window, out_dtype="float32")
                else:
                    with WarpedVRT(
                        dataset,
                        crs=reference.crs,
                        transform=reference.transform,
                        width=reference.width,
                        height=reference.height,
                        resampling=resampling,
                    ) as warped:
                        array = warped.read(bands, window=window, out_dtype="float32")
    return array


def normalize_sar(array: np.ndarray) -> np.ndarray:
    """Clip calibrated sigma0 dB and map the stable interval to [-1, 1]."""
    array = np.nan_to_num(array, nan=-30.0, posinf=5.0, neginf=-30.0)
    return ((np.clip(array, -30.0, 5.0) + 30.0) / 35.0 * 2.0 - 1.0).astype(np.float32)


def normalize_optical(array: np.ndarray) -> np.ndarray:
    """Normalize Sentinel-2 SR reflectance stored on the standard 0..10000 scale."""
    array = np.nan_to_num(array, nan=0.0, posinf=10_000.0, neginf=0.0)
    return (np.clip(array, 0.0, 10_000.0) / 10_000.0).astype(np.float32)


def normalize_aux(array: np.ndarray) -> np.ndarray:
    if array.shape[0] < len(AUX_BANDS):
        raise ValueError(f"auxiliary raster needs {len(AUX_BANDS)} bands, got {array.shape[0]}")
    result = np.nan_to_num(array[: len(AUX_BANDS)], nan=0.0, posinf=0.0, neginf=0.0)
    scales = np.asarray([45.0, 50.0, 100.0, 12.0, 1.0, 1.0], dtype=np.float32)
    result = np.clip(result / scales[:, None, None], 0.0, 1.0)
    return result.astype(np.float32)


class GeoPatchDataset(Dataset[dict[str, torch.Tensor | str]]):
    """Random but index-deterministic aligned patches from complete S1/S2 pairs."""

    def __init__(
        self,
        records: list[PairRecord],
        *,
        patch_size: int = 384,
        samples_per_epoch: int = 4096,
        positive_fraction: float = 0.65,
        optical_dropout: float = 0.5,
        augment: bool = True,
        seed: int = 2026,
        max_positive_centres: int = 200_000,
    ) -> None:
        if not records:
            raise ValueError("records cannot be empty")
        if patch_size <= 0 or samples_per_epoch <= 0:
            raise ValueError("patch_size and samples_per_epoch must be positive")
        if not 0.0 <= positive_fraction <= 1.0:
            raise ValueError("positive_fraction must be in [0, 1]")
        if not 0.0 <= optical_dropout <= 1.0:
            raise ValueError("optical_dropout must be in [0, 1]")

        self.records = records
        self.patch_size = patch_size
        self.samples_per_epoch = samples_per_epoch
        self.positive_fraction = positive_fraction
        self.optical_dropout = optical_dropout
        self.augment = augment
        self.seed = seed
        self._epoch = 0
        self._positive_centres = self._index_positive_centres(max_positive_centres)
        self._positive_records = [
            index for index, centres in self._positive_centres.items() if len(centres)
        ]

    def set_epoch(self, epoch: int) -> None:
        self._epoch = int(epoch)

    def __len__(self) -> int:
        return self.samples_per_epoch

    def _index_positive_centres(self, limit: int) -> dict[int, np.ndarray]:
        result: dict[int, np.ndarray] = {}
        for index, record in enumerate(self.records):
            with rasterio.open(record.reference_mask) as dataset:
                flood = dataset.read(1)
            flat = np.flatnonzero(flood == 1)
            if len(flat) > limit:
                rng = np.random.default_rng(self.seed + index)
                flat = rng.choice(flat, size=limit, replace=False)
            rows, cols = np.unravel_index(flat, flood.shape)
            result[index] = np.column_stack((rows, cols)).astype(np.int32, copy=False)
        return result

    def _window(self, record_index: int, rng: np.random.Generator, positive: bool) -> Window:
        record = self.records[record_index]
        with rasterio.open(record.reference_mask) as dataset:
            height, width = dataset.height, dataset.width
        if height < self.patch_size or width < self.patch_size:
            raise ValueError(
                f"{record.pair_id}: raster {width}x{height} is smaller than patch {self.patch_size}"
            )

        if positive and len(self._positive_centres[record_index]):
            row, col = self._positive_centres[record_index][
                rng.integers(len(self._positive_centres[record_index]))
            ]
            row_off = int(np.clip(row - self.patch_size // 2, 0, height - self.patch_size))
            col_off = int(np.clip(col - self.patch_size // 2, 0, width - self.patch_size))
        else:
            row_off = int(rng.integers(0, height - self.patch_size + 1))
            col_off = int(rng.integers(0, width - self.patch_size + 1))
        return Window(col_off, row_off, self.patch_size, self.patch_size)

    @staticmethod
    def _augment(arrays: list[np.ndarray], rng: np.random.Generator) -> list[np.ndarray]:
        rotation = int(rng.integers(0, 4))
        flip_y = bool(rng.integers(0, 2))
        flip_x = bool(rng.integers(0, 2))
        output: list[np.ndarray] = []
        for array in arrays:
            transformed = np.rot90(array, rotation, axes=(-2, -1))
            if flip_y:
                transformed = np.flip(transformed, axis=-2)
            if flip_x:
                transformed = np.flip(transformed, axis=-1)
            output.append(np.ascontiguousarray(transformed))
        return output

    def __getitem__(self, index: int) -> dict[str, torch.Tensor | str]:
        rng = np.random.default_rng(self.seed + self._epoch * self.samples_per_epoch + index)
        positive = bool(self._positive_records) and rng.random() < self.positive_fraction
        if positive:
            record_index = int(rng.choice(self._positive_records))
        else:
            record_index = int(rng.integers(len(self.records)))
        record = self.records[record_index]
        window = self._window(record_index, rng, positive)

        s1_paths = (record.scene("S1_pre"), record.scene("S1_peak"))
        if any(path is None for path in s1_paths):
            raise FileNotFoundError(
                f"{record.pair_id}: both S1_pre and S1_peak GeoTIFFs are required"
            )
        sar = np.stack(
            [normalize_sar(_read_bands(path, (1, 2), window)) for path in s1_paths if path],
            axis=0,
        )

        optical_shape = (4, self.patch_size, self.patch_size)
        optical_arrays: list[np.ndarray] = []
        optical_available: list[float] = []
        for stem in ("SENTINEL2_pre", "SENTINEL2_peak"):
            path = record.scene(stem)
            if path is None:
                optical_arrays.append(np.zeros(optical_shape, dtype=np.float32))
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
        target = _read_bands(record.reference_mask, (1, 2, 3, 4, 5), window)
        target = np.clip(target, 0.0, 1.0).astype(np.float32)
        # The official masks declare nodata=0 even though zero is also the valid
        # negative class. Rasterio's dataset_mask would therefore keep only
        # positive pixels and corrupt the loss. The full reference grid is valid.
        valid = np.ones((1, self.patch_size, self.patch_size), dtype=np.float32)

        if self.augment:
            sar, optical, aux, target, valid = self._augment(
                [sar, optical, aux, target, valid], rng
            )

        if self.optical_dropout and rng.random() < self.optical_dropout:
            optical.fill(0.0)
            optical_available = [0.0, 0.0]

        return {
            "pair_id": record.pair_id,
            "sar": torch.from_numpy(np.ascontiguousarray(sar)),
            "optical": torch.from_numpy(np.ascontiguousarray(optical)),
            "aux": torch.from_numpy(np.ascontiguousarray(aux)),
            "target": torch.from_numpy(np.ascontiguousarray(target)),
            "valid": torch.from_numpy(np.ascontiguousarray(valid)),
            "optical_available": torch.tensor(optical_available, dtype=torch.float32),
        }


def load_reference_stats(record: PairRecord) -> dict[str, float]:
    with record.reference_metadata.open(encoding="utf-8") as stream:
        payload = json.load(stream)
    return {
        key: float(value)
        for key, value in payload["stats"].items()
        if isinstance(value, int | float)
    }
