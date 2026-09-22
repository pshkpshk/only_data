from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

pytest.importorskip("fastapi")
pytest.importorskip("shapely")
if not any(importlib.util.find_spec(m) for m in ("httpx2", "httpx")):
    pytest.skip("TestClient needs httpx2/httpx (dev extra)", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from hydrowatch.service.app import create_app  # noqa: E402
from hydrowatch.service.config import ServiceConfig  # noqa: E402

PAIR_ID = "flood_test__aoi"


@pytest.fixture()
def service_root(tmp_path: Path) -> Path:
    """predictions/ with one 3-band mask + pairs.csv + AUX raster on the same grid."""
    root = tmp_path / "svc"
    mask_dir = root / "predictions" / "masks"
    mask_dir.mkdir(parents=True)
    height = width = 40
    masks = np.zeros((3, height, width), dtype=np.uint8)
    masks[1, 5:15, 5:15] = 1  # water_pre: 100 px = 1 ha
    masks[2, 5:20, 5:15] = 1  # water_peak: 150 px = 1.5 ha
    masks[0] = masks[2] & ~masks[1]  # flood: 50 px = 0.5 ha
    transform = from_origin(500_000, 5_600_000, 10, 10)
    profile = dict(
        driver="GTiff",
        width=width,
        height=height,
        count=3,
        dtype="uint8",
        crs="EPSG:32652",
        transform=transform,
    )
    with rasterio.open(mask_dir / f"{PAIR_ID}_all.tif", "w", **profile) as dataset:
        dataset.write(masks)
    aux_dir = root / "data" / "rasters" / "flood_test" / "aoi"
    aux_dir.mkdir(parents=True)
    aux = np.zeros((6, height, width), dtype=np.float32)
    aux[1] = 3.0  # hand < 5 → lowland
    aux[5, 5:8, 5:15] = 1.0  # some built-up inside the flood
    with rasterio.open(
        aux_dir / "AUX_terrain_gsw.tif", "w", **(profile | {"count": 6, "dtype": "float32"})
    ) as dataset:
        dataset.write(aux)
    with (root / "data" / "pairs.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "pair_id",
                "aoi_id",
                "aoi_name",
                "event_id",
                "event_name",
                "event_kind",
                "sensor_sar",
                "sensor_optical",
                "date_pre_sar",
                "date_peak_sar",
                "date_pre_opt",
                "date_peak_opt",
                "orbit_pass",
                "relative_orbit",
                "rasters_dir",
                "reference_mask",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "pair_id": PAIR_ID,
                "aoi_id": "aoi",
                "aoi_name": "Test AOI",
                "event_id": "flood_test",
                "event_name": "Test flood",
                "event_kind": "rain_flood",
                "sensor_sar": "sentinel1",
                "sensor_optical": "",
                "date_pre_sar": "2020-06-01",
                "date_peak_sar": "2020-07-01",
                "date_pre_opt": "",
                "date_peak_opt": "",
                "orbit_pass": "DESCENDING",
                "relative_orbit": "32",
                "rasters_dir": "rasters/flood_test/aoi",
                "reference_mask": f"reference_masks/reference_{PAIR_ID}.tif",
            }
        )
    return root


@pytest.fixture()
def client(service_root: Path) -> TestClient:
    config = ServiceConfig(
        predictions_dir=service_root / "predictions",
        data_root=service_root / "data",
        state_dir=service_root / "state",
    )
    return TestClient(create_app(config))


def test_catalog_and_whole_aoi_report_reproduce_mask_areas(client: TestClient) -> None:
    pairs = client.get("/api/pairs").json()
    assert pairs["count"] == 1
    assert pairs["pairs"][0]["pair_id"] == PAIR_ID
    report = client.get(f"/api/pairs/{PAIR_ID}/report.json").json()
    assert report["water_surface"]["pre"]["ha"] == pytest.approx(1.0)
    assert report["water_surface"]["peak"]["ha"] == pytest.approx(1.5)
    assert report["change"]["flood_gain"]["ha"] == pytest.approx(0.5)
    assert report["change"]["receded_loss"]["ha"] == pytest.approx(0.0)
    assert report["change"]["net_change"]["sign"] == "+"
    breakdown = {row["code"]: row["flood_ha"] for row in report["landcover_breakdown"]}
    assert breakdown["builtup"] == pytest.approx(
        0.0
    )  # built-up rows lie inside water_pre, not flood
    assert breakdown["lowland"] == pytest.approx(0.5)


def test_spatio_temporal_query_resolves_pair_and_clips(client: TestClient) -> None:
    footprint = client.get(f"/api/pairs/{PAIR_ID}").json()["bbox_wgs84"]
    min_lon, min_lat, max_lon, max_lat = footprint
    # bbox covering only the western half of the raster
    body = {
        "bbox": [min_lon, min_lat, (min_lon + max_lon) / 2, max_lat],
        "date_pre": "2020-06-05",
        "date_peak": "2020-07-03",
    }
    response = client.post("/api/analyze", json=body)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["pair"]["pair_id"] == PAIR_ID
    assert report["request"]["resolution"]["mode"] == "resolved_by_space_and_time"
    assert 0.4 < report["area"]["analyzed_area"]["ha"] / (40 * 40 * 0.01) < 0.6
    assert report["change"]["flood_gain"]["ha"] <= 0.5
    job = report["job_id"]
    geojson = client.get(f"/api/jobs/{job}/vectors/flood.geojson").json()
    assert geojson["type"] == "FeatureCollection"
    assert geojson["features"], "flood polygons expected in the western half"
    feature = geojson["features"][0]
    assert feature["properties"]["type"] == "flood"
    assert feature["properties"]["area_ha"] > 0
    lon, lat = feature["geometry"]["coordinates"][0][0]
    assert 120 < lon < 135 and 45 < lat < 55  # WGS84, Amur region of UTM zone 52
    png = client.get(f"/api/jobs/{job}/overlay/flood.png")
    assert png.status_code == 200 and png.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert "X-Bounds" in png.headers
    csv_text = client.get(f"/api/jobs/{job}/report.csv").text
    assert csv_text.startswith("key,value")
    assert "change.flood_gain.ha" in csv_text


def test_query_outside_dates_lists_available_pairs(client: TestClient) -> None:
    footprint = client.get(f"/api/pairs/{PAIR_ID}").json()["bbox_wgs84"]
    response = client.post(
        "/api/analyze",
        json={"bbox": footprint, "date_pre": "2015-01-01", "date_peak": "2015-02-01"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["available_pairs"][0]["pair_id"] == PAIR_ID


def test_incremental_observation_recomputes_only_differences(
    client: TestClient, service_root: Path
) -> None:
    mask_path = service_root / "predictions" / "masks" / f"{PAIR_ID}_all.tif"
    with rasterio.open(mask_path) as dataset:
        profile = dataset.profile.copy()
        peak = dataset.read(3)
    new_water = peak.copy()
    new_water[5:10, 5:15] = 0  # water receded on the northern rows
    new_path = service_root / "new_scene.tif"
    with rasterio.open(new_path, "w", **(profile | {"count": 1})) as dataset:
        dataset.write(new_water, 1)
    with new_path.open("rb") as stream:
        response = client.post(
            "/api/observations",
            data={"aoi_id": "aoi", "observed_on": "2020-07-13", "baseline_pair_id": PAIR_ID},
            files={"water_mask": ("new_scene.tif", stream, "image/tiff")},
        )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["water_new_ha"] == pytest.approx(1.0)
    assert payload["flood_gain_ha"] == pytest.approx(0.5)
    assert payload["receded_loss_ha"] == pytest.approx(0.5)
    assert (service_root / "state" / "aoi_2020-07-13_water.tif").is_file()
