from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import rasterio
from rasterio.warp import transform_bounds, transform_geom

from hydrowatch.service.config import ServiceConfig

WGS84 = "EPSG:4326"


@dataclass(frozen=True)
class PairEntry:
    pair_id: str
    aoi_id: str
    aoi_name: str
    event_id: str
    event_name: str
    event_kind: str
    date_pre: date
    date_peak: date
    date_pre_opt: date | None
    date_peak_opt: date | None
    sensor_sar: str
    sensor_optical: str
    orbit_pass: str
    relative_orbit: str
    mask_path: Path
    aux_path: Path | None
    crs: str
    width: int
    height: int
    pixel_area_ha: float
    bounds_native: tuple[float, float, float, float]
    bounds_wgs84: tuple[float, float, float, float]
    footprint_wgs84: dict

    @property
    def aoi_ha(self) -> float:
        return self.width * self.height * self.pixel_area_ha

    def to_json(self) -> dict:
        return {
            "pair_id": self.pair_id,
            "aoi_id": self.aoi_id,
            "aoi_name": self.aoi_name,
            "event_id": self.event_id,
            "event_name": self.event_name,
            "event_kind": self.event_kind,
            "date_pre": self.date_pre.isoformat(),
            "date_peak": self.date_peak.isoformat(),
            "date_pre_optical": self.date_pre_opt.isoformat() if self.date_pre_opt else None,
            "date_peak_optical": self.date_peak_opt.isoformat() if self.date_peak_opt else None,
            "sensors": [s for s in (self.sensor_sar, self.sensor_optical) if s],
            "orbit": {"pass": self.orbit_pass, "relative_orbit": self.relative_orbit},
            "crs": self.crs,
            "grid": {
                "width": self.width,
                "height": self.height,
                "pixel_area_ha": self.pixel_area_ha,
            },
            "aoi_ha": round(self.aoi_ha, 2),
            "bbox_wgs84": [round(v, 6) for v in self.bounds_wgs84],
            "footprint_wgs84": self.footprint_wgs84,
            "has_aux": self.aux_path is not None,
        }


def _parse_date(value: str | None) -> date | None:
    value = (value or "").strip()
    return date.fromisoformat(value) if value else None


def load_catalog(config: ServiceConfig) -> list[PairEntry]:
    """Read pairs.csv and attach the predicted 3-band mask of every pair."""
    pairs_csv = config.resolve_pairs_csv()
    mask_dir = config.predictions_dir / "masks"
    entries: list[PairEntry] = []
    with pairs_csv.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            pair_id = row["pair_id"]
            mask_path = mask_dir / f"{pair_id}_all.tif"
            if not mask_path.is_file():
                continue  # the service only serves pairs that were actually predicted
            aux_path = None
            if config.data_root is not None:
                candidate = config.data_root / row["rasters_dir"] / "AUX_terrain_gsw.tif"
                aux_path = candidate if candidate.is_file() else None
            with rasterio.open(mask_path) as dataset:
                bounds = tuple(dataset.bounds)
                crs = dataset.crs
                width, height = dataset.width, dataset.height
                pixel_area_ha = abs(dataset.transform.a * dataset.transform.e) / 10_000.0
            wgs_bounds = transform_bounds(crs, WGS84, *bounds, densify_pts=21)
            left, bottom, right, top = bounds
            footprint = {
                "type": "Polygon",
                "coordinates": [
                    [[left, bottom], [left, top], [right, top], [right, bottom], [left, bottom]]
                ],
            }
            footprint_wgs84 = transform_geom(crs, WGS84, footprint, precision=6)
            date_pre = _parse_date(row.get("date_pre_sar"))
            date_peak = _parse_date(row.get("date_peak_sar"))
            if date_pre is None or date_peak is None:
                raise ValueError(f"{pair_id}: pairs.csv must define SAR dates for the pair")
            entries.append(
                PairEntry(
                    pair_id=pair_id,
                    aoi_id=row["aoi_id"],
                    aoi_name=row.get("aoi_name", row["aoi_id"]),
                    event_id=row["event_id"],
                    event_name=row.get("event_name", row["event_id"]),
                    event_kind=row.get("event_kind", ""),
                    date_pre=date_pre,
                    date_peak=date_peak,
                    date_pre_opt=_parse_date(row.get("date_pre_opt")),
                    date_peak_opt=_parse_date(row.get("date_peak_opt")),
                    sensor_sar=row.get("sensor_sar", ""),
                    sensor_optical=row.get("sensor_optical", ""),
                    orbit_pass=row.get("orbit_pass", ""),
                    relative_orbit=row.get("relative_orbit", ""),
                    mask_path=mask_path,
                    aux_path=aux_path,
                    crs=str(crs),
                    width=width,
                    height=height,
                    pixel_area_ha=pixel_area_ha,
                    bounds_native=bounds,  # type: ignore[arg-type]
                    bounds_wgs84=tuple(wgs_bounds),  # type: ignore[arg-type]
                    footprint_wgs84=footprint_wgs84,
                )
            )
    if not entries:
        raise FileNotFoundError(f"no predicted masks found under {mask_dir}")
    return entries
