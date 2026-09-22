from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from datetime import date
from pathlib import Path
from typing import Annotated, Any

import numpy as np
import rasterio
from fastapi import FastAPI, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from hydrowatch.service.analysis import (
    LAYER_TITLES,
    LAYERS,
    build_report,
    dissolve_outline,
    geometry_from_request,
    layer_masks,
    overlay_png,
    report_to_csv,
    spatial_overlap,
    vectorize,
)
from hydrowatch.service.catalog import PairEntry, load_catalog
from hydrowatch.service.config import ServiceConfig

WEB_DIR = Path(__file__).resolve().parents[3] / "web"


# --------------------------------------------------------------------------- request models
class AnalyzeRequest(BaseModel):
    """Spatio-temporal query: polygon or bbox (WGS84) plus a pair of dates or intervals."""

    geometry: dict[str, Any] | None = Field(default=None, description="GeoJSON geometry, EPSG:4326")
    bbox: list[float] | None = Field(
        default=None, description="[min_lon, min_lat, max_lon, max_lat]"
    )
    date_pre: date | None = Field(default=None, description="дата «до» (или начало интервала)")
    date_pre_end: date | None = Field(
        default=None, description="конец интервала «до» (опционально)"
    )
    date_peak: date | None = Field(
        default=None, description="дата «после/пик» (или начало интервала)"
    )
    date_peak_end: date | None = Field(
        default=None, description="конец интервала «после» (опционально)"
    )
    pair_id: str | None = Field(default=None, description="явный выбор пары вместо поиска по датам")
    clip_to_geometry: bool = Field(
        default=True, description="обрезать результат по геометрии запроса"
    )


class JobStore:
    """In-memory registry of resolved queries (pair + geometry) for follow-up downloads."""

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}

    def put(self, payload: dict[str, Any]) -> str:
        job_id = uuid.uuid4().hex[:12]
        self._jobs[job_id] = payload
        return job_id

    def get(self, job_id: str) -> dict[str, Any]:
        if job_id not in self._jobs:
            raise HTTPException(
                404, f"job {job_id} not found (service restarted?) — repeat POST /api/analyze"
            )
        return self._jobs[job_id]


# --------------------------------------------------------------------------- pair resolution
def _within(value: date, start: date | None, end: date | None, tolerance: int) -> bool:
    if start is None:
        return True
    if end is None:
        return abs((value - start).days) <= tolerance
    return start <= value <= end


def resolve_pair(
    catalog: list[PairEntry],
    config: ServiceConfig,
    request: AnalyzeRequest,
    geometry: dict | None,
) -> tuple[PairEntry, dict[str, Any]]:
    by_id = {entry.pair_id: entry for entry in catalog}
    if request.pair_id:
        if request.pair_id not in by_id:
            raise HTTPException(404, f"unknown pair_id {request.pair_id}; see GET /api/pairs")
        entry = by_id[request.pair_id]
        overlap = spatial_overlap(entry, geometry) if geometry else 1.0
        if geometry and overlap == 0.0:
            raise HTTPException(422, f"geometry does not intersect pair {entry.pair_id}")
        return entry, {"mode": "explicit_pair", "spatial_overlap": round(overlap, 4)}

    if geometry is None:
        raise HTTPException(422, "geometry or bbox is required when pair_id is not given")
    candidates = []
    for entry in catalog:
        overlap = spatial_overlap(entry, geometry)
        if overlap <= 0.0:
            continue
        temporal_ok = _within(
            entry.date_pre, request.date_pre, request.date_pre_end, config.date_tolerance_days
        ) and _within(
            entry.date_peak, request.date_peak, request.date_peak_end, config.date_tolerance_days
        )
        candidates.append((entry, overlap, temporal_ok))
    if not candidates:
        raise HTTPException(
            404, "no prepared pair covers the requested area; see GET /api/pairs for footprints"
        )
    matching = [c for c in candidates if c[2]]
    if not matching:
        available = [
            {
                "pair_id": e.pair_id,
                "date_pre": e.date_pre.isoformat(),
                "date_peak": e.date_peak.isoformat(),
                "overlap": round(o, 3),
            }
            for e, o, _ in sorted(candidates, key=lambda c: -c[1])
        ]
        raise HTTPException(
            404,
            {
                "message": (
                    "the area is covered, but no prepared observation pair matches "
                    "the requested dates "
                    f"(tolerance ±{config.date_tolerance_days} d). Available pairs for this area:"
                ),
                "available_pairs": available,
            },
        )

    # prefer the pair with the largest spatial overlap; ties → closest peak date
    def sort_key(item: tuple[PairEntry, float, bool]) -> tuple[float, int]:
        entry, overlap, _ = item
        distance = abs((entry.date_peak - request.date_peak).days) if request.date_peak else 0
        return (-overlap, distance)

    entry, overlap, _ = sorted(matching, key=sort_key)[0]
    return entry, {
        "mode": "resolved_by_space_and_time",
        "spatial_overlap": round(overlap, 4),
        "alternatives": [e.pair_id for e, _, _ in matching if e.pair_id != entry.pair_id],
    }


# --------------------------------------------------------------------------- app factory
def create_app(config: ServiceConfig | None = None) -> FastAPI:
    config = config or ServiceConfig.load()
    catalog = load_catalog(config)
    jobs = JobStore()
    app = FastAPI(
        title="HydroWatch Amur — сервис мониторинга затопления",
        description=(
            "REST API поверх масок Siamese U-Net: водное зеркало на две даты, прирост/убыль, "
            "векторные контуры и сводный отчёт по произвольному полигону и датам."
        ),
        version="1.0.0",
    )
    app.state.config = config
    app.state.catalog = catalog

    def entry_by_id(pair_id: str) -> PairEntry:
        for entry in catalog:
            if entry.pair_id == pair_id:
                return entry
        raise HTTPException(404, f"unknown pair_id {pair_id}")

    def _job_context(job_id: str) -> tuple[PairEntry, dict | None]:
        job = jobs.get(job_id)
        return entry_by_id(job["pair_id"]), job["geometry"]

    def _downloads(prefix: str) -> dict[str, Any]:
        return {
            "report_json": f"{prefix}/report.json",
            "report_csv": f"{prefix}/report.csv",
            "vectors_geojson": {layer: f"{prefix}/vectors/{layer}.geojson" for layer in LAYERS},
            "overlays_png": {layer: f"{prefix}/overlay/{layer}.png" for layer in LAYERS},
            "outline_geojson": f"{prefix}/outline.geojson",
        }

    # ----------------------------------------------------------------- meta
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "pairs": len(catalog), "layers": list(LAYERS)}

    @app.get("/api/layers")
    def layers() -> dict[str, str]:
        return LAYER_TITLES

    @app.get("/api/pairs")
    def pairs() -> dict[str, Any]:
        return {"count": len(catalog), "pairs": [entry.to_json() for entry in catalog]}

    @app.get("/api/pairs/{pair_id}")
    def pair(pair_id: str) -> dict[str, Any]:
        entry = entry_by_id(pair_id)
        return {**entry.to_json(), "downloads": _downloads(f"/api/pairs/{pair_id}")}

    # ----------------------------------------------------------------- spatio-temporal query
    @app.post("/api/analyze")
    def analyze(request: AnalyzeRequest) -> dict[str, Any]:
        try:
            geometry = (
                geometry_from_request(request.geometry, request.bbox)
                if (request.geometry is not None or request.bbox is not None)
                else None
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        entry, resolution = resolve_pair(catalog, config, request, geometry)
        clip_geometry = geometry if (geometry is not None and request.clip_to_geometry) else None
        echo = request.model_dump(mode="json") | {"resolution": resolution}
        try:
            report = build_report(entry, config, clip_geometry, echo)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        job_id = jobs.put({"pair_id": entry.pair_id, "geometry": clip_geometry, "report": report})
        report["job_id"] = job_id
        report["downloads"] = _downloads(f"/api/jobs/{job_id}")
        return report

    @app.get("/api/jobs/{job_id}/report.json")
    def job_report(job_id: str) -> dict[str, Any]:
        return jobs.get(job_id)["report"]

    @app.get("/api/jobs/{job_id}/report.csv")
    def job_report_csv(job_id: str) -> Response:
        csv_text = report_to_csv(jobs.get(job_id)["report"])
        return Response(
            csv_text,
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="hydrowatch_report_{job_id}.csv"'
            },
        )

    @app.get("/api/jobs/{job_id}/vectors/{layer}.geojson")
    def job_vectors(job_id: str, layer: str) -> Response:
        entry, geometry = _job_context(job_id)
        return _geojson_response(
            vectorize(entry, layer, config, geometry), f"{entry.pair_id}_{layer}.geojson"
        )

    @app.get("/api/jobs/{job_id}/overlay/{layer}.png")
    def job_overlay(job_id: str, layer: str) -> Response:
        entry, geometry = _job_context(job_id)
        return _png_response(entry, layer, geometry)

    @app.get("/api/jobs/{job_id}/outline.geojson")
    def job_outline(job_id: str) -> dict[str, Any]:
        entry, geometry = _job_context(job_id)
        return {
            "type": "Feature",
            "geometry": dissolve_outline(geometry, entry),
            "properties": {"pair_id": entry.pair_id},
        }

    # ------------------------------------------------------- per-pair shortcuts (whole AOI)
    @app.get("/api/pairs/{pair_id}/report.json")
    def pair_report(pair_id: str) -> dict[str, Any]:
        entry = entry_by_id(pair_id)
        report = build_report(entry, config, None, {"pair_id": pair_id, "mode": "whole_aoi"})
        report["downloads"] = _downloads(f"/api/pairs/{pair_id}")
        return report

    @app.get("/api/pairs/{pair_id}/report.csv")
    def pair_report_csv(pair_id: str) -> Response:
        entry = entry_by_id(pair_id)
        report = build_report(entry, config, None, {"pair_id": pair_id, "mode": "whole_aoi"})
        return Response(
            report_to_csv(report),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="hydrowatch_report_{pair_id}.csv"'
            },
        )

    @app.get("/api/pairs/{pair_id}/vectors/{layer}.geojson")
    def pair_vectors(pair_id: str, layer: str) -> Response:
        entry = entry_by_id(pair_id)
        return _geojson_response(
            vectorize(entry, layer, config, None), f"{pair_id}_{layer}.geojson"
        )

    @app.get("/api/pairs/{pair_id}/overlay/{layer}.png")
    def pair_overlay(pair_id: str, layer: str) -> Response:
        return _png_response(entry_by_id(pair_id), layer, None)

    @app.get("/api/pairs/{pair_id}/outline.geojson")
    def pair_outline(pair_id: str) -> dict[str, Any]:
        entry = entry_by_id(pair_id)
        return {
            "type": "Feature",
            "geometry": entry.footprint_wgs84,
            "properties": {"pair_id": pair_id},
        }

    @app.get("/api/pairs/{pair_id}/mask.tif")
    def pair_mask(pair_id: str, layer: str = Query(default="all")) -> Response:
        entry = entry_by_id(pair_id)
        if layer == "all":
            return FileResponse(
                entry.mask_path, media_type="image/tiff", filename=entry.mask_path.name
            )
        if layer not in LAYERS:
            raise HTTPException(422, f"layer must be one of {LAYERS} or 'all'")
        mask = layer_masks(entry)[layer].astype(np.uint8)
        with rasterio.open(entry.mask_path) as reference:
            profile = reference.profile.copy()
        profile.update(count=1, dtype="uint8", compress="deflate")
        with rasterio.io.MemoryFile() as memory:
            with memory.open(**profile) as dataset:
                dataset.write(mask, 1)
            payload = memory.read()
        return Response(
            payload,
            media_type="image/tiff",
            headers={"Content-Disposition": f'attachment; filename="{pair_id}_{layer}.tif"'},
        )

    # ----------------------------------------------------------------- incremental update
    @app.post("/api/observations")
    async def register_observation(
        aoi_id: Annotated[str, Form(description="район интереса, для которого пришла сцена")],
        observed_on: Annotated[date, Form(description="дата новой радиолокационной сцены")],
        baseline_pair_id: Annotated[str, Form(description="пара, чьё состояние «до» опорное")],
        water_mask: Annotated[
            UploadFile, File(description="GeoTIFF uint8 0/1 маски воды новой сцены на сетке AOI")
        ],
    ) -> dict[str, Any]:
        """Инкрементальное обновление: новая маска воды сравнивается с сохранённым состоянием «до».

        Хранится: опорная маска water_pre (из baseline_pair_id) и последняя принятая маска воды
        по AOI. Пересчитывается: только прирост/убыль относительно опорного состояния —
        без повторного инференса по всей паре.
        """
        base = entry_by_id(baseline_pair_id)
        if base.aoi_id != aoi_id:
            raise HTTPException(
                422, f"baseline pair {baseline_pair_id} belongs to AOI {base.aoi_id}, not {aoi_id}"
            )
        payload = await water_mask.read()
        with rasterio.io.MemoryFile(payload) as memory, memory.open() as new_scene:
            with rasterio.open(base.mask_path) as reference:
                same_grid = (
                    new_scene.shape == reference.shape
                    and new_scene.crs == reference.crs
                    and new_scene.transform.almost_equals(reference.transform)
                )
                if not same_grid:
                    raise HTTPException(
                        422, "uploaded mask must share grid/CRS/transform with the AOI masks"
                    )
            new_water = new_scene.read(1).astype(bool)
        masks = layer_masks(base)
        pre = masks["water_pre"]
        flood_new = new_water & ~pre
        receded_new = pre & ~new_water
        px = base.pixel_area_ha
        config.state_dir.mkdir(parents=True, exist_ok=True)
        state_path = config.state_dir / f"{aoi_id}_{observed_on.isoformat()}_water.tif"
        state_path.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()[:16]
        result = {
            "aoi_id": aoi_id,
            "observed_on": observed_on.isoformat(),
            "baseline_pair_id": baseline_pair_id,
            "baseline_pre_date": base.date_pre.isoformat(),
            "stored_state": str(state_path),
            "sha256_16": digest,
            "water_new_ha": round(float(new_water.sum() * px), 2),
            "water_pre_ha": round(float(pre.sum() * px), 2),
            "flood_gain_ha": round(float(flood_new.sum() * px), 2),
            "receded_loss_ha": round(float(receded_new.sum() * px), 2),
            "delta_vs_last_peak_ha": round(
                float((new_water.sum() - masks["water_peak"].sum()) * px), 2
            ),
            "note": "пересчитаны только разностные слои; "
            "маска «до» и AUX-контекст взяты из хранилища",
        }
        (config.state_dir / f"{aoi_id}_{observed_on.isoformat()}_summary.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return result

    # ----------------------------------------------------------------- helpers & static UI
    def _geojson_response(collection: dict, filename: str) -> Response:
        return Response(
            json.dumps(collection, ensure_ascii=False),
            media_type="application/geo+json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    def _png_response(entry: PairEntry, layer: str, geometry: dict | None) -> Response:
        try:
            payload, bounds = overlay_png(entry, layer, config, geometry)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        return Response(
            payload,
            media_type="image/png",
            headers={
                "X-Bounds": json.dumps(bounds),
                "Cache-Control": "max-age=3600",
                "Access-Control-Expose-Headers": "X-Bounds",
            },
        )

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    else:  # pragma: no cover

        @app.get("/")
        def index() -> JSONResponse:
            return JSONResponse({"message": "web UI not found; API docs at /docs"})

    return app


# --------------------------------------------------------------------------- CLI entry point
def main() -> None:
    parser = argparse.ArgumentParser(description="Run the HydroWatch analytical service")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--config", default=None, help="YAML config (default configs/service.yaml)")
    parser.add_argument("--predictions-dir", default=None)
    parser.add_argument("--data-root", default=None)
    args = parser.parse_args()

    import os

    if args.predictions_dir:
        os.environ["HYDROWATCH_PREDICTIONS_DIR"] = args.predictions_dir
    if args.data_root is not None:
        os.environ["HYDROWATCH_DATA_ROOT"] = args.data_root
    if args.config:
        os.environ["HYDROWATCH_SERVICE_CONFIG"] = args.config

    import uvicorn

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")


app = None  # created lazily by `uvicorn hydrowatch.service.app:create_app --factory`

if __name__ == "__main__":
    main()
