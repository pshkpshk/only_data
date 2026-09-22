from __future__ import annotations

import csv
from pathlib import Path

from hydrowatch.download import build_tasks, load_overrides


def test_scene_override_changes_orbit_and_adds_fallback(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    fields = [
        "pair_id",
        "rasters_dir",
        "reference_mask",
        "orbit_pass",
        "relative_orbit",
        "date_pre_sar",
        "date_peak_sar",
        "date_pre_opt",
        "date_peak_opt",
    ]
    with (root / "pairs.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerow(
            {
                "pair_id": "event__aoi",
                "rasters_dir": "rasters/event/aoi",
                "reference_mask": "reference.tif",
                "orbit_pass": "DESCENDING",
                "relative_orbit": "105",
                "date_pre_sar": "2021-05-14",
                "date_peak_sar": "2021-07-01",
                "date_pre_opt": "2021-05-18",
                "date_peak_opt": "2021-06-27",
            }
        )
    overrides_path = tmp_path / "overrides.yaml"
    overrides_path.write_text(
        """
event__aoi:
  s1:
    pre:
      date: '2021-04-27'
      relative_orbit: 32
      output_name: S1_pre.tif
  s2:
    peak:
      fallback_dates: ['2021-06-24']
      output_name: SENTINEL2_peak.tif
""",
        encoding="utf-8",
    )

    tasks = build_tasks(root, {"s1", "s2"}, load_overrides(overrides_path))
    indexed = {(task.sensor, task.window): task for task in tasks}

    assert indexed["s1", "pre"].date == "2021-04-27"
    assert indexed["s1", "pre"].relative_orbit == 32
    assert indexed["s1", "pre"].output.name == "S1_pre.tif"
    assert indexed["s2", "peak"].dates == ("2021-06-27", "2021-06-24")
    assert indexed["s2", "peak"].output.name == "SENTINEL2_peak.tif"
