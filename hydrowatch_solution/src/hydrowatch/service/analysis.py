from __future__ import annotations

import struct
import zlib
from datetime import UTC, datetime
from functools import lru_cache

import numpy as np
import rasterio
from rasterio import features
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.warp import (
    Resampling as WarpResampling,
)
from rasterio.warp import (
    calculate_default_transform,
    reproject,
    transform_bounds,
    transform_geom,
)
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from hydrowatch.service.catalog import WGS84, PairEntry
from hydrowatch.service.config import ServiceConfig

LAYERS = ("water_pre", "water_peak", "flood", "receded")
LAYER_TITLES = {
    "water_pre": "Водное зеркало на дату «до»",
    "water_peak": "Водное зеркало на дату пика",
    "flood": "Зона нового затопления (прирост)",
    "receded": "Ушедшая вода (убыль)",
}
LAYER_COLORS = {
    "water_pre": (31, 119, 180, 200),
    "water_peak": (0, 176, 246, 200),
    "flood": (230, 57, 70, 230),
    "receded": (255, 183, 3, 220),
}
AUX_BANDS = ("slope", "hand", "occurrence", "seasonality", "max_extent", "builtup")
WEB_MERCATOR = "EPSG:3857"


# --------------------------------------------------------------------------- raster access
@lru_cache(maxsize=4)
def _load_masks(mask_path: str) -> dict[str, np.ndarray]:
    with rasterio.open(mask_path) as dataset:
        flood, water_pre, water_peak = dataset.read().astype(bool)
    return {
        "flood": flood,
        "water_pre": water_pre,
        "water_peak": water_peak,
        "receded": water_pre & ~water_peak,
    }


@lru_cache(maxsize=3)
def _load_aux(aux_path: str, mask_path: str) -> dict[str, np.ndarray]:
    """AUX raster (30 m) resampled by nearest neighbour onto the 10 m mask grid.

    Stored compactly (bool flags + float16 HAND): ~80 MB per pair instead of ~320 MB.
    """
    with rasterio.open(mask_path) as reference, rasterio.open(aux_path) as aux:
        with WarpedVRT(
            aux,
            crs=reference.crs,
            transform=reference.transform,
            width=reference.width,
            height=reference.height,
            resampling=Resampling.nearest,
        ) as warped:
            array = warped.read(out_dtype="float32")
    array = np.nan_to_num(array, nan=0.0)
    bands = {name: array[index] for index, name in enumerate(AUX_BANDS)}
    return {
        "hand": bands["hand"].astype(np.float16),
        "slope": bands["slope"].astype(np.float16),
        "occurrence": bands["occurrence"].astype(np.uint8),
        "seasonality": bands["seasonality"].astype(np.uint8),
        "max_extent": bands["max_extent"] > 0.5,
        "builtup": bands["builtup"] > 0.5,
    }


def layer_masks(entry: PairEntry) -> dict[str, np.ndarray]:
    return _load_masks(str(entry.mask_path))


def aux_bands(entry: PairEntry) -> dict[str, np.ndarray] | None:
    if entry.aux_path is None:
        return None
    return _load_aux(str(entry.aux_path), str(entry.mask_path))


def _transform(entry: PairEntry) -> rasterio.Affine:
    with rasterio.open(entry.mask_path) as dataset:
        return dataset.transform


# --------------------------------------------------------------------------- geometry helpers
def geometry_from_request(geometry: dict | None, bbox: list[float] | None) -> dict:
    if geometry is None and bbox is None:
        raise ValueError(
            "either 'geometry' (GeoJSON, WGS84) or 'bbox' [minx,miny,maxx,maxy] is required"
        )
    if geometry is not None:
        geom = shape(geometry)
    else:
        assert bbox is not None
        if len(bbox) != 4:
            raise ValueError("bbox must have 4 numbers: min_lon, min_lat, max_lon, max_lat")
        minx, miny, maxx, maxy = bbox
        if minx >= maxx or miny >= maxy:
            raise ValueError("bbox must satisfy min_lon < max_lon and min_lat < max_lat")
        geom = shape(
            {
                "type": "Polygon",
                "coordinates": [
                    [[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]
                ],
            }
        )
    if geom.is_empty or not geom.is_valid:
        geom = geom.buffer(0)
    if geom.is_empty:
        raise ValueError("query geometry is empty or invalid")
    return mapping(geom)


def spatial_overlap(entry: PairEntry, geometry_wgs84: dict) -> float:
    """Fraction of the query geometry covered by the pair footprint (computed in WGS84)."""
    query = shape(geometry_wgs84)
    footprint = shape(entry.footprint_wgs84)
    if query.area == 0:
        return 0.0
    return float(query.intersection(footprint).area / query.area)


def region_mask(entry: PairEntry, geometry_wgs84: dict | None) -> np.ndarray | None:
    """Boolean array on the pair grid: True inside the requested geometry. None = whole AOI."""
    if geometry_wgs84 is None:
        return None
    native = transform_geom(WGS84, entry.crs, geometry_wgs84)
    return features.geometry_mask(
        [native],
        out_shape=(entry.height, entry.width),
        transform=_transform(entry),
        invert=True,
        all_touched=False,
    )


# --------------------------------------------------------------------------- report
def _area(count: int, pixel_area_ha: float) -> dict[str, float]:
    ha = count * pixel_area_ha
    return {"ha": round(ha, 2), "km2": round(ha / 100.0, 4)}


def _landcover_breakdown(
    entry: PairEntry, flood: np.ndarray, config: ServiceConfig
) -> tuple[list[dict], str]:
    aux = aux_bands(entry)
    flood_px = int(flood.sum())
    if aux is None:
        return (
            [],
            "AUX_terrain_gsw.tif недоступен (не смонтирован data_root) — разбивка не рассчитана",
        )
    if flood_px == 0:
        return [], "затопления в запрошенной области нет"
    remaining = flood.copy()
    rules = {
        "builtup": aux["builtup"],
        "permanent_water": aux["occurrence"] >= config.permanent_occurrence_pct,
        "seasonal_water": aux["seasonality"] >= 1,
        "floodplain": aux["max_extent"],
        "lowland": aux["hand"] < config.lowland_hand_m,
        "upland": np.ones_like(flood, dtype=bool),
    }
    rows = []
    for item in config.landcover_classes:
        code = item["code"]
        selected = remaining & rules[code]
        count = int(selected.sum())
        remaining &= ~selected
        rows.append(
            {
                "code": code,
                "class": item["name"],
                **{f"flood_{k}": v for k, v in _area(count, entry.pixel_area_ha).items()},
                "share_of_flood": round(count / flood_px, 4),
            }
        )
    return (
        rows,
        "классы получены из AUX_terrain_gsw.tif (WorldCover built-up, JRC GSW, MERIT HAND); "
        "первый подходящий класс побеждает",
    )


def _hand_profile(entry: PairEntry, flood: np.ndarray) -> list[dict] | None:
    aux = aux_bands(entry)
    if aux is None or not flood.any():
        return None
    hand = aux["hand"][flood].astype(np.float32)
    bins = [0, 2, 5, 10, 15, 25, np.inf]
    labels = ["0–2 м", "2–5 м", "5–10 м", "10–15 м", "15–25 м", "> 25 м"]
    counts, _ = np.histogram(np.clip(hand, 0, None), bins=bins)
    total = max(int(counts.sum()), 1)
    return [
        {
            "hand_range": label,
            "flood_ha": round(int(c) * entry.pixel_area_ha, 2),
            "share": round(int(c) / total, 4),
        }
        for label, c in zip(labels, counts, strict=True)
    ]


def build_report(
    entry: PairEntry,
    config: ServiceConfig,
    geometry_wgs84: dict | None,
    request_echo: dict,
) -> dict:
    masks = layer_masks(entry)
    region = region_mask(entry, geometry_wgs84)
    if region is None:
        region = np.ones((entry.height, entry.width), dtype=bool)
        requested_px = int(region.size)
    else:
        requested_px = int(region.sum())
    if requested_px == 0:
        raise ValueError("the requested geometry does not intersect the pair footprint")

    px = entry.pixel_area_ha
    counts = {name: int(np.count_nonzero(mask & region)) for name, mask in masks.items()}
    aux = aux_bands(entry)
    permanent_px = (
        int(np.count_nonzero((aux["occurrence"] >= config.permanent_occurrence_pct) & region))
        if aux is not None
        else None
    )
    net_px = counts["water_peak"] - counts["water_pre"]
    requested = _area(requested_px, px)
    landcover, landcover_note = _landcover_breakdown(entry, masks["flood"] & region, config)

    if geometry_wgs84 is not None:
        query_area_ha = _geodesic_area_ha(geometry_wgs84)
        coverage = min(1.0, requested["ha"] / query_area_ha) if query_area_ha > 0 else None
    else:
        query_area_ha = requested["ha"]
        coverage = 1.0

    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "request": request_echo,
        "pair": entry.to_json(),
        "area": {
            "requested_query_area_ha": round(query_area_ha, 2),
            "analyzed_area": requested,
            "analyzed_share_of_query": round(coverage, 4) if coverage is not None else None,
            "note": "площади считаются подсчётом пикселей 10 м в EPSG:32652 (UTM 52N); "
            "analyzed_area — пересечение запроса с растром пары",
        },
        "water_surface": {
            "pre": {
                **_area(counts["water_pre"], px),
                "date": entry.date_pre.isoformat(),
                "share_of_area": round(counts["water_pre"] / requested_px, 4),
            },
            "peak": {
                **_area(counts["water_peak"], px),
                "date": entry.date_peak.isoformat(),
                "share_of_area": round(counts["water_peak"] / requested_px, 4),
            },
            "permanent": (_area(permanent_px, px) if permanent_px is not None else None),
        },
        "change": {
            "flood_gain": {
                **_area(counts["flood"], px),
                "share_of_area": round(counts["flood"] / requested_px, 4),
                "definition": "вода на пике, которой не было «до» и которая не постоянная "
                "(канал flood модели)",
            },
            "receded_loss": {
                **_area(counts["receded"], px),
                "share_of_area": round(counts["receded"] / requested_px, 4),
                "definition": "water_pre & ~water_peak",
            },
            "net_change": {
                **_area(abs(net_px), px) | {"sign": "+" if net_px >= 0 else "-"},
                "pct_of_pre": (
                    round(100.0 * net_px / counts["water_pre"], 2) if counts["water_pre"] else None
                ),
            },
        },
        "landcover_breakdown": landcover,
        "landcover_note": landcover_note,
        "hand_profile_of_flood": _hand_profile(entry, masks["flood"] & region),
        "provenance": {
            "mask_file": entry.mask_path.name,
            "model": config.model_name,
            "method": "маски получены инференсом hydrowatch-predict; "
            "сервис не обращается к облачным платформам",
        },
    }
    return report


def _geodesic_area_ha(geometry_wgs84: dict) -> float:
    """Approximate query area (ha) by projecting to an equal-area CRS."""
    try:
        projected = transform_geom(
            WGS84, "ESRI:54034", geometry_wgs84
        )  # World Cylindrical Equal Area
        return float(shape(projected).area / 10_000.0)
    except Exception:  # pragma: no cover - projection database issues
        return float(shape(geometry_wgs84).area * 111_000 * 111_000 / 10_000.0)


def report_to_csv(report: dict) -> str:
    """Flatten the report to key,value rows (machine readable, opens in Excel)."""
    rows: list[tuple[str, object]] = []

    skip = {"footprint_wgs84", "geometry", "downloads"}  # bulky/non-tabular fields

    def walk(prefix: str, value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in skip:
                    continue
                walk(f"{prefix}.{key}" if prefix else str(key), item)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(f"{prefix}[{index}]", item)
        else:
            rows.append((prefix, value))

    walk("", report)
    lines = ["key,value"]
    for key, value in rows:
        text = "" if value is None else str(value).replace('"', '""')
        lines.append(f'{key},"{text}"')
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- vectors
def vectorize(
    entry: PairEntry,
    layer: str,
    config: ServiceConfig,
    geometry_wgs84: dict | None,
) -> dict:
    if layer not in LAYERS:
        raise ValueError(f"unknown layer {layer!r}; expected one of {LAYERS}")
    mask = layer_masks(entry)[layer]
    region = region_mask(entry, geometry_wgs84)
    if region is not None:
        mask = mask & region
    transform = _transform(entry)
    min_px = int(round(config.min_mapping_unit_ha / entry.pixel_area_ha))
    features_out = []
    dropped = 0
    for index, (geom, value) in enumerate(
        features.shapes(mask.astype(np.uint8), mask=mask, transform=transform, connectivity=8)
    ):
        if value != 1:
            continue
        polygon = shape(geom)
        area_ha = polygon.area / 10_000.0
        if area_ha < config.min_mapping_unit_ha:
            dropped += 1
            continue
        if config.simplify_tolerance_m > 0:
            polygon = polygon.simplify(config.simplify_tolerance_m, preserve_topology=True)
        features_out.append(
            {
                "type": "Feature",
                "id": f"{entry.pair_id}:{layer}:{index}",
                "geometry": transform_geom(entry.crs, WGS84, mapping(polygon), precision=6),
                "properties": {
                    "id": index,
                    "type": layer,
                    "title": LAYER_TITLES[layer],
                    "area_ha": round(area_ha, 2),
                    "area_km2": round(area_ha / 100.0, 4),
                    "pair_id": entry.pair_id,
                    "aoi_id": entry.aoi_id,
                    "date_pre": entry.date_pre.isoformat(),
                    "date_peak": entry.date_peak.isoformat(),
                },
            }
        )
    total_ha = round(sum(f["properties"]["area_ha"] for f in features_out), 2)
    return {
        "type": "FeatureCollection",
        "name": f"{entry.pair_id}_{layer}",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features_out,
        "properties": {
            "layer": layer,
            "pair_id": entry.pair_id,
            "source_crs": entry.crs,
            "min_mapping_unit_ha": config.min_mapping_unit_ha,
            "min_mapping_unit_px": min_px,
            "dropped_small_polygons": dropped,
            "total_area_ha": total_ha,
            "simplify_tolerance_m": config.simplify_tolerance_m,
        },
    }


def dissolve_outline(geometry_wgs84: dict | None, entry: PairEntry) -> dict:
    """Outline of the analysed region (query ∩ footprint) as GeoJSON in WGS84."""
    footprint = shape(entry.footprint_wgs84)
    if geometry_wgs84 is not None:
        footprint = unary_union([footprint]).intersection(shape(geometry_wgs84))
    return mapping(footprint)


# --------------------------------------------------------------------------- overlays
def _encode_png(rgba: np.ndarray) -> bytes:
    """Minimal dependency-free PNG encoder for an (H, W, 4) uint8 array."""
    height, width, _ = rgba.shape
    raw = np.concatenate(
        [np.zeros((height, 1), dtype=np.uint8), rgba.reshape(height, width * 4)], axis=1
    )

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw.tobytes(), 6))
        + chunk(b"IEND", b"")
    )


def overlay_png(
    entry: PairEntry,
    layer: str,
    config: ServiceConfig,
    geometry_wgs84: dict | None,
) -> tuple[bytes, list[list[float]]]:
    """Web-Mercator RGBA overlay of one layer plus its lat/lon bounds for Leaflet."""
    if layer not in LAYERS:
        raise ValueError(f"unknown layer {layer!r}; expected one of {LAYERS}")
    mask = layer_masks(entry)[layer]
    region = region_mask(entry, geometry_wgs84)
    if region is not None:
        mask = mask & region
    src_transform = _transform(entry)
    scale = max(1.0, max(entry.width, entry.height) / config.overlay_max_size)
    dst_transform, dst_width, dst_height = calculate_default_transform(
        entry.crs,
        WEB_MERCATOR,
        entry.width,
        entry.height,
        *entry.bounds_native,
        dst_width=int(round(entry.width / scale)),
        dst_height=int(round(entry.height / scale)),
    )
    destination = np.zeros((dst_height, dst_width), dtype=np.uint8)
    reproject(
        source=mask.astype(np.uint8) * 255,
        destination=destination,
        src_transform=src_transform,
        src_crs=entry.crs,
        dst_transform=dst_transform,
        dst_crs=WEB_MERCATOR,
        resampling=WarpResampling.average,  # anti-aliased downsampling of the binary mask
        src_nodata=None,
        dst_nodata=0,
    )
    r, g, b, a = LAYER_COLORS[layer]
    rgba = np.zeros((dst_height, dst_width, 4), dtype=np.uint8)
    rgba[..., 0] = r
    rgba[..., 1] = g
    rgba[..., 2] = b
    rgba[..., 3] = (destination.astype(np.float32) * (a / 255.0)).astype(np.uint8)
    left = dst_transform.c
    top = dst_transform.f
    right = left + dst_transform.a * dst_width
    bottom = top + dst_transform.e * dst_height
    min_lon, min_lat, max_lon, max_lat = transform_bounds(
        WEB_MERCATOR, WGS84, left, bottom, right, top
    )
    return _encode_png(rgba), [[min_lat, min_lon], [max_lat, max_lon]]
