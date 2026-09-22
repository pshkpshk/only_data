from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin


def _write_raster(path: Path, data: np.ndarray, *, dtype: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=data.shape[-1],
        height=data.shape[-2],
        count=data.shape[0],
        dtype=dtype,
        crs="EPSG:32652",
        transform=from_origin(500_000, 5_600_000, 10, 10),
        compress="deflate",
    ) as dataset:
        dataset.write(data.astype(dtype))


@pytest.fixture()
def tiny_dataset(tmp_path: Path) -> Path:
    root = tmp_path / "hydrowatch"
    pair_id = "flood_test__aoi"
    raster_dir = root / "rasters" / "flood_test" / "aoi"
    reference_path = root / "reference_masks" / f"reference_{pair_id}.tif"
    height = width = 32

    reference = np.zeros((5, height, width), dtype=np.uint8)
    reference[0, 10:14, 12:17] = 1
    reference[1, 8:12, 8:12] = 1
    reference[2] = np.maximum(reference[0], reference[1])
    reference[3, 2:5, 2:5] = 1
    reference[4, 20:22, 20:24] = 1
    _write_raster(reference_path, reference, dtype="uint8")

    aux = np.zeros((6, height, width), dtype=np.float32)
    aux[0] = 2.0
    aux[1] = 5.0
    aux[2] = 10.0
    _write_raster(raster_dir / "AUX_terrain_gsw.tif", aux, dtype="float32")
    for stem in ("S1_pre_2020-01-01", "S1_peak_2020-01-10"):
        _write_raster(
            raster_dir / f"{stem}.tif",
            np.full((2, height, width), -18.0, dtype=np.float32),
            dtype="float32",
        )
    for stem in ("SENTINEL2_pre_2020-01-02", "SENTINEL2_peak_2020-01-09"):
        _write_raster(
            raster_dir / f"{stem}.tif",
            np.full((4, height, width), 2_000.0, dtype=np.float32),
            dtype="float32",
        )

    pixel_area_ha = 0.01
    stats = {
        f"{name}_ha": float(reference[index].sum() * pixel_area_ha)
        for index, name in enumerate(("flood", "water_pre", "water_peak", "permanent", "receded"))
    }
    reference_path.with_suffix(".json").write_text(
        json.dumps({"pair_id": pair_id, "stats": stats}), encoding="utf-8"
    )
    root.mkdir(parents=True, exist_ok=True)
    with (root / "pairs.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "pair_id",
                "aoi_id",
                "event_id",
                "event_kind",
                "sensor_optical",
                "rasters_dir",
                "reference_mask",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "pair_id": pair_id,
                "aoi_id": "aoi",
                "event_id": "flood_test",
                "event_kind": "rain_flood",
                "sensor_optical": "sentinel2",
                "rasters_dir": "rasters/flood_test/aoi",
                "reference_mask": f"reference_masks/reference_{pair_id}.tif",
            }
        )
    return root
