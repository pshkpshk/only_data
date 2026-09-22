from __future__ import annotations

import argparse
import csv
import json
import os
from collections.abc import Iterable
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import planetary_computer
import rasterio
import yaml
from pystac import Item
from pystac_client import Client
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
from tqdm import tqdm

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
S1_ASSETS = ("vv", "vh")
S2_ASSETS = ("B03", "B04", "B08", "B11")


@dataclass(frozen=True)
class DownloadTask:
    pair_id: str
    sensor: str
    window: str
    date: str
    output: Path
    reference: Path
    orbit_pass: str | None = None
    relative_orbit: int | None = None
    fallback_dates: tuple[str, ...] = ()

    @property
    def dates(self) -> tuple[str, ...]:
        return (self.date, *self.fallback_dates)


def _optional_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    return int(float(value))


def load_overrides(path: Path | None) -> dict[str, object]:
    if path is None or not path.is_file():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"scene overrides must be a mapping: {path}")
    return payload


def _override(
    overrides: dict[str, object], pair_id: str, sensor: str, window: str
) -> dict[str, object]:
    value = overrides.get(pair_id, {})
    if not isinstance(value, dict):
        raise ValueError(f"override for {pair_id} must be a mapping")
    value = value.get(sensor, {})
    if not isinstance(value, dict):
        raise ValueError(f"override for {pair_id}/{sensor} must be a mapping")
    value = value.get(window, {})
    if not isinstance(value, dict):
        raise ValueError(f"override for {pair_id}/{sensor}/{window} must be a mapping")
    return value


def build_tasks(
    root: Path, sensors: set[str], overrides: dict[str, object] | None = None
) -> list[DownloadTask]:
    overrides = overrides or {}
    tasks: list[DownloadTask] = []
    with (root / "pairs.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            raster_dir = root / row["rasters_dir"]
            reference = root / row["reference_mask"]
            for window in ("pre", "peak"):
                if "s1" in sensors:
                    settings = _override(overrides, row["pair_id"], "s1", window)
                    date = str(settings.get("date", row[f"date_{window}_sar"])).strip()
                    output_name = str(
                        settings.get("output_name", f"S1_{window}_{date}.tif")
                    )
                    tasks.append(
                        DownloadTask(
                            pair_id=row["pair_id"],
                            sensor="s1",
                            window=window,
                            date=date,
                            output=raster_dir / output_name,
                            reference=reference,
                            orbit_pass=str(
                                settings.get("orbit_pass", row["orbit_pass"])
                            ).strip()
                            or None,
                            relative_orbit=_optional_int(
                                str(settings.get("relative_orbit", row["relative_orbit"]))
                            ),
                            fallback_dates=tuple(
                                str(value) for value in settings.get("fallback_dates", [])
                            ),
                        )
                    )
                if "s2" in sensors:
                    settings = _override(overrides, row["pair_id"], "s2", window)
                    date = str(settings.get("date", row[f"date_{window}_opt"])).strip()
                    if date:
                        output_name = str(
                            settings.get("output_name", f"SENTINEL2_{window}_{date}.tif")
                        )
                        tasks.append(
                            DownloadTask(
                                pair_id=row["pair_id"],
                                sensor="s2",
                                window=window,
                                date=date,
                                output=raster_dir / output_name,
                                reference=reference,
                                fallback_dates=tuple(
                                    str(value) for value in settings.get("fallback_dates", [])
                                ),
                            )
                        )
    return tasks


def _matches_s1_orbit(item: Item, task: DownloadTask) -> bool:
    properties = item.properties
    orbit_state = str(properties.get("sat:orbit_state", "")).upper()
    relative_orbit = properties.get("sat:relative_orbit")
    pass_matches = task.orbit_pass is None or orbit_state == task.orbit_pass.upper()
    orbit_matches = task.relative_orbit is None or int(relative_orbit) == task.relative_orbit
    return pass_matches and orbit_matches


def find_items(catalog: Client, task: DownloadTask) -> list[Item]:
    with rasterio.open(task.reference) as reference:
        bbox = transform_bounds(reference.crs, "EPSG:4326", *reference.bounds)
    collection = "sentinel-1-rtc" if task.sensor == "s1" else "sentinel-2-l2a"
    result: list[Item] = []
    for acquisition_date in task.dates:
        day = f"{acquisition_date}T00:00:00Z/{acquisition_date}T23:59:59Z"
        items = list(catalog.search(collections=[collection], bbox=bbox, datetime=day).items())
        if task.sensor == "s1":
            items = [item for item in items if _matches_s1_orbit(item, task)]
        result.extend(sorted(items, key=lambda item: item.id))
    return result


def _task_key(task: DownloadTask) -> str:
    return f"{task.pair_id}:{task.sensor}:{task.window}"


def write_manifest(catalog: Client, tasks: list[DownloadTask], path: Path) -> None:
    manifest: dict[str, list[dict[str, object]]] = {}
    for task in tqdm(tasks, desc="Resolving STAC scenes"):
        items = find_items(catalog, task)
        if not items:
            raise RuntimeError(f"{_task_key(task)}: no matching scene on {task.date}")
        manifest[_task_key(task)] = [item.to_dict() for item in items]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    try:
        temporary.write_text(json.dumps(manifest), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_manifest(path: Path) -> dict[str, list[Item]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(key): [Item.from_dict(item) for item in items]
        for key, items in payload.items()
    }


def _mosaic_asset(
    items: Iterable[Item],
    asset_name: str,
    reference: rasterio.io.DatasetReader,
    resampling: Resampling,
) -> np.ndarray:
    result = np.full((reference.height, reference.width), np.nan, dtype=np.float32)
    with ExitStack() as stack:
        for item in items:
            asset = item.assets.get(asset_name)
            if asset is None:
                continue
            source = stack.enter_context(rasterio.open(asset.href))
            warped = stack.enter_context(
                WarpedVRT(
                    source,
                    crs=reference.crs,
                    transform=reference.transform,
                    width=reference.width,
                    height=reference.height,
                    src_nodata=source.nodata,
                    nodata=np.nan,
                    resampling=resampling,
                )
            )
            values = warped.read(1, masked=True, out_dtype="float32")
            valid = (
                ~np.ma.getmaskarray(values)
                & np.isfinite(values.data)
                & ~np.isfinite(result)
            )
            result[valid] = values.data[valid]
    if not np.any(np.isfinite(result)):
        raise RuntimeError(f"no valid pixels found for asset {asset_name}")
    return result


def _write_atomic(task: DownloadTask, bands: np.ndarray, descriptions: tuple[str, ...]) -> None:
    task.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = task.output.with_suffix(task.output.suffix + ".part")
    with rasterio.open(task.reference) as reference:
        profile = reference.profile.copy()
    profile.update(
        driver="GTiff",
        count=len(descriptions),
        dtype="float32",
        nodata=-9999.0,
        compress="deflate",
        predictor=3,
        tiled=True,
        blockxsize=512,
        blockysize=512,
        BIGTIFF="IF_SAFER",
    )
    values = np.nan_to_num(bands, nan=-9999.0, posinf=-9999.0, neginf=-9999.0)
    try:
        with rasterio.open(temporary, "w", **profile) as output:
            output.write(values.astype(np.float32, copy=False))
            output.descriptions = descriptions
            output.update_tags(
                source="Microsoft Planetary Computer STAC",
                source_collection=("sentinel-1-rtc" if task.sensor == "s1" else "sentinel-2-l2a"),
                acquisition_dates=",".join(task.dates),
                pair_id=task.pair_id,
            )
        os.replace(temporary, task.output)
    finally:
        temporary.unlink(missing_ok=True)


def download_task(task: DownloadTask, items: list[Item]) -> tuple[int, float]:
    if not items:
        raise RuntimeError(
            f"{task.pair_id} {task.sensor}/{task.window}: no matching scene on {task.date}"
        )
    with rasterio.Env(
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        GDAL_HTTP_MULTIRANGE="YES",
        GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
        VSI_CACHE=True,
        VSI_CACHE_SIZE=64_000_000,
    ):
        with rasterio.open(task.reference) as reference:
            if task.sensor == "s1":
                linear = np.stack(
                    [
                        _mosaic_asset(items, asset, reference, Resampling.bilinear)
                        for asset in S1_ASSETS
                    ]
                )
                with np.errstate(divide="ignore", invalid="ignore"):
                    db = 10.0 * np.log10(np.maximum(linear, 1e-6))
                bands = np.stack((db[0], db[1], db[0] - db[1]))
                descriptions = ("VV_db", "VH_db", "VV_minus_VH_db")
            else:
                bands = np.stack(
                    [
                        _mosaic_asset(items, asset, reference, Resampling.bilinear)
                        for asset in S2_ASSETS
                    ]
                )
                descriptions = ("B03_green", "B04_red", "B08_nir", "B11_swir")
    _write_atomic(task, bands, descriptions)
    valid_fraction = float(np.all(np.isfinite(bands), axis=0).mean())
    return len(items), valid_fraction


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export aligned Sentinel-1/2 rasters from public Planetary Computer COGs."
    )
    parser.add_argument("--root", type=Path, default=Path("data/raw/hydrowatch_amur"))
    parser.add_argument("--sensor", choices=("s1", "s2", "all"), default="all")
    parser.add_argument(
        "--pair", action="append", default=[], help="Restrict to pair_id; repeatable"
    )
    parser.add_argument("--limit", type=int, default=None, help="Process at most this many tasks")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--write-manifest", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--overrides",
        type=Path,
        default=Path("configs/scene_overrides.yaml"),
        help="Optional YAML with per-pair acquisition overrides and fallback dates",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.root.expanduser().resolve()
    sensors = {"s1", "s2"} if args.sensor == "all" else {args.sensor}
    overrides = load_overrides(args.overrides.expanduser().resolve() if args.overrides else None)
    tasks = build_tasks(root, sensors, overrides)
    if args.pair:
        requested = set(args.pair)
        tasks = [task for task in tasks if task.pair_id in requested]
        missing = requested - {task.pair_id for task in tasks}
        if missing:
            raise SystemExit(f"unknown or sensor-incompatible pair_id values: {sorted(missing)}")
    if not args.overwrite:
        tasks = [task for task in tasks if not task.output.exists()]
    if args.limit is not None:
        if args.limit < 1:
            raise SystemExit("--limit must be positive")
        tasks = tasks[: args.limit]

    if args.dry_run:
        for task in tasks:
            print(f"{task.sensor} {task.pair_id} {task.window} {task.date} -> {task.output}")
        print(f"tasks={len(tasks)}")
        return

    catalog: Client | None = None
    manifest: dict[str, list[Item]] | None = None
    if args.manifest:
        manifest = read_manifest(args.manifest.expanduser().resolve())
    else:
        catalog = Client.open(STAC_URL, modifier=planetary_computer.sign_inplace)
    if args.write_manifest:
        if catalog is None:
            raise SystemExit("--write-manifest cannot be combined with --manifest")
        write_manifest(catalog, tasks, args.write_manifest.expanduser().resolve())
        print(f"manifest={args.write_manifest} tasks={len(tasks)}")
        return

    failures: list[str] = []
    for task in tqdm(tasks, desc="Sentinel exports"):
        try:
            if manifest is not None:
                items = manifest.get(_task_key(task), [])
            else:
                assert catalog is not None
                items = find_items(catalog, task)
            item_count, valid_fraction = download_task(task, items)
            tqdm.write(
                f"ok {task.output.name}: items={item_count}, valid={valid_fraction:.3%}"
            )
        except Exception as error:  # keep the overnight batch moving and report all failures
            message = f"FAILED {task.pair_id} {task.sensor}/{task.window}: {error}"
            failures.append(message)
            tqdm.write(message)
    if failures:
        raise SystemExit("\n".join(failures))


if __name__ == "__main__":
    main()
