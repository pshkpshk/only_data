from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT

from hydrowatch.data import AUX_BANDS, MASK_BANDS, load_pairs


def _date_gap_days(pre: object, peak: object) -> float:
    if pd.isna(pre) or pd.isna(peak) or str(pre).strip() == "" or str(peak).strip() == "":
        return float("nan")
    return float((pd.Timestamp(peak) - pd.Timestamp(pre)).days)


def _mask_profile(mask_path: Path) -> dict[str, float | int | str | bool]:
    counts = np.zeros(len(MASK_BANDS), dtype=np.int64)
    logic = {
        "flood_not_peak_px": 0,
        "flood_and_pre_px": 0,
        "flood_and_permanent_px": 0,
        "flood_formula_disagreement_px": 0,
        "receded_formula_disagreement_px": 0,
    }
    with rasterio.open(mask_path) as dataset:
        for _, window in dataset.block_windows(1):
            block = dataset.read(window=window).astype(bool)
            counts += block.sum(axis=(1, 2), dtype=np.int64)
            flood, water_pre, water_peak, permanent, receded = block
            expected_flood = water_peak & ~water_pre & ~permanent
            expected_receded = water_pre & ~water_peak
            logic["flood_not_peak_px"] += int(np.count_nonzero(flood & ~water_peak))
            logic["flood_and_pre_px"] += int(np.count_nonzero(flood & water_pre))
            logic["flood_and_permanent_px"] += int(np.count_nonzero(flood & permanent))
            logic["flood_formula_disagreement_px"] += int(np.count_nonzero(flood ^ expected_flood))
            logic["receded_formula_disagreement_px"] += int(
                np.count_nonzero(receded ^ expected_receded)
            )
        pixel_area_ha = abs(dataset.transform.a * dataset.transform.e) / 10_000.0
        profile: dict[str, float | int | str | bool] = {
            "width": dataset.width,
            "height": dataset.height,
            "raster_pixels": dataset.width * dataset.height,
            "pixel_area_ha": pixel_area_ha,
            "reference_crs": str(dataset.crs),
            "reference_resolution_m": abs(dataset.res[0]),
            "reference_nodata": str(dataset.nodata),
        }
    for index, name in enumerate(MASK_BANDS):
        profile[f"{name}_pixels"] = int(counts[index])
        profile[f"{name}_ha_mask"] = float(counts[index] * pixel_area_ha)
    profile.update(logic)
    return profile


def build_pair_profile(root: str | Path) -> pd.DataFrame:
    root = Path(root).expanduser().resolve()
    pairs = pd.read_csv(root / "pairs.csv")
    records = {record.pair_id: record for record in load_pairs(root)}
    rows: list[dict[str, object]] = []
    for raw in pairs.to_dict(orient="records"):
        pair_id = str(raw["pair_id"])
        record = records[pair_id]
        with record.reference_metadata.open(encoding="utf-8") as stream:
            metadata = json.load(stream)
        stats = metadata["stats"]
        mask = _mask_profile(record.reference_mask)
        with rasterio.open(record.aux) as aux:
            aux_resolution = abs(aux.res[0])
            aux_aligned = (
                aux.width == mask["width"]
                and aux.height == mask["height"]
                and str(aux.crs) == mask["reference_crs"]
                and np.isclose(aux_resolution, mask["reference_resolution_m"])
            )
        fallback_aoi_ha = float(mask["raster_pixels"]) * float(mask["pixel_area_ha"])
        aoi_ha = float(stats.get("aoi_ha", fallback_aoi_ha))
        row: dict[str, object] = {
            **raw,
            **mask,
            "aoi_ha": aoi_ha,
            "has_optical_pair": record.has_optical,
            "sar_gap_days": _date_gap_days(raw.get("date_pre_sar"), raw.get("date_peak_sar")),
            "opt_gap_days": _date_gap_days(raw.get("date_pre_opt"), raw.get("date_peak_opt")),
            "pre_cross_sensor_gap_days": abs(
                _date_gap_days(raw.get("date_pre_sar"), raw.get("date_pre_opt"))
            ),
            "peak_cross_sensor_gap_days": abs(
                _date_gap_days(raw.get("date_peak_sar"), raw.get("date_peak_opt"))
            ),
            "aux_resolution_m": aux_resolution,
            "aux_aligned": aux_aligned,
            "s1_files_present": all(
                record.scene(stem) is not None for stem in ("S1_pre", "S1_peak")
            ),
            "s2_files_present": all(
                record.scene(stem) is not None for stem in ("SENTINEL2_pre", "SENTINEL2_peak")
            ),
        }
        for name in MASK_BANDS:
            metadata_area = float(stats[f"{name}_ha"])
            mask_area = float(mask[f"{name}_ha_mask"])
            row[f"{name}_ha_metadata"] = metadata_area
            row[f"{name}_metadata_delta_ha"] = mask_area - metadata_area
            row[f"{name}_share_aoi_pct"] = mask_area / aoi_ha * 100.0
        rows.append(row)
    return pd.DataFrame(rows)


def build_aux_label_profile(root: str | Path) -> pd.DataFrame:
    root = Path(root).expanduser().resolve()
    rows: list[dict[str, object]] = []
    for record in load_pairs(root):
        with rasterio.open(record.aux) as aux, rasterio.open(record.reference_mask) as mask:
            with WarpedVRT(
                mask,
                crs=aux.crs,
                transform=aux.transform,
                width=aux.width,
                height=aux.height,
                resampling=Resampling.nearest,
            ) as aligned_mask:
                flood = aligned_mask.read(1).astype(bool)
            values = aux.read((1, 2, 3), out_dtype="float32")
        for band_index, band_name in enumerate(AUX_BANDS[:3]):
            band = values[band_index]
            finite = np.isfinite(band)
            for label, selection in (
                ("flood", finite & flood),
                ("background", finite & ~flood),
            ):
                selected = band[selection]
                if selected.size == 0:
                    continue
                rows.append(
                    {
                        "pair_id": record.pair_id,
                        "event_kind": record.event_kind,
                        "feature": band_name,
                        "label": label,
                        "pixels_30m": int(selected.size),
                        "median": float(np.median(selected)),
                        "p10": float(np.quantile(selected, 0.1)),
                        "p90": float(np.quantile(selected, 0.9)),
                    }
                )
    return pd.DataFrame(rows)


def build_era5_profile(root: str | Path) -> pd.DataFrame:
    root = Path(root).expanduser().resolve()
    pairs = pd.read_csv(root / "pairs.csv")
    rows: list[dict[str, object]] = []
    for pair in pairs.to_dict(orient="records"):
        raster_dir = root / str(pair["rasters_dir"])
        matches = sorted(raster_dir.glob("ERA5_daily_*.csv"))
        if len(matches) != 1:
            continue
        weather = pd.read_csv(matches[0], parse_dates=["date"])
        start = pd.Timestamp(pair["date_pre_sar"])
        end = pd.Timestamp(pair["date_peak_sar"])
        interval = weather.loc[weather["date"].between(start, end)]
        rows.append(
            {
                "pair_id": pair["pair_id"],
                "event_id": pair["event_id"],
                "event_kind": pair["event_kind"],
                "days": int(len(interval)),
                "precip_mm_sum": float(interval["precip_mm"].sum()),
                "temp_c_mean": float(interval["temp_c"].mean()),
                "snowmelt_mm_sum": float(interval["snowmelt_mm"].sum()),
            }
        )
    return pd.DataFrame(rows)


def export_eda_tables(root: str | Path, output_dir: str | Path) -> dict[str, Path]:
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    tables = {
        "pair_profile": build_pair_profile(root),
        "aux_label_profile": build_aux_label_profile(root),
        "era5_profile": build_era5_profile(root),
    }
    paths: dict[str, Path] = {}
    for name, table in tables.items():
        path = output_dir / f"{name}.csv"
        table.to_csv(path, index=False)
        paths[name] = path
    return paths
