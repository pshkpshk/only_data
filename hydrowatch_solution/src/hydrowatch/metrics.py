from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class AreaResult:
    pair_id: str
    event_kind: str
    aoi_ha: float
    predicted_flood_ha: float
    predicted_water_pre_ha: float
    predicted_water_peak_ha: float
    target_flood_ha: float
    target_water_pre_ha: float
    target_water_peak_ha: float


def _area_agreement(predicted: float, target: float, floor_ha: float) -> float:
    return max(0.0, 1.0 - abs(predicted - target) / max(target, floor_ha))


def competition_components(results: Iterable[AreaResult]) -> dict[str, float]:
    rows = list(results)
    events = [row for row in rows if row.event_kind != "baseline"]
    baselines = [row for row in rows if row.event_kind == "baseline"]
    if not events:
        raise ValueError("at least one event row is required")
    if not baselines:
        raise ValueError("at least one baseline row is required")

    q_flood = sum(
        _area_agreement(row.predicted_flood_ha, row.target_flood_ha, 50.0) for row in events
    ) / len(events)
    q_water_peak = sum(
        _area_agreement(row.predicted_water_peak_ha, row.target_water_peak_ha, 200.0)
        for row in events
    ) / len(events)
    q_water_pre = sum(
        _area_agreement(row.predicted_water_pre_ha, row.target_water_pre_ha, 200.0)
        for row in events
    ) / len(events)
    specificity = sum(
        1.0
        - min(
            1.0,
            max(0.0, row.predicted_flood_ha - row.target_flood_ha) / row.aoi_ha / 0.005,
        )
        for row in baselines
    ) / len(baselines)
    score = 0.45 * q_flood + 0.25 * q_water_peak + 0.15 * q_water_pre + 0.15 * specificity
    return {
        "q_flood": q_flood,
        "q_water_peak": q_water_peak,
        "q_water_pre": q_water_pre,
        "spec_base": specificity,
        "score": score,
    }
