from __future__ import annotations

import pytest

from hydrowatch.metrics import AreaResult, competition_components


def test_competition_score_is_one_for_exact_areas() -> None:
    rows = [
        AreaResult("event", "rain_flood", 1_000, 100, 300, 400, 100, 300, 400),
        AreaResult("baseline", "baseline", 1_000, 0, 300, 300, 0, 300, 300),
    ]
    components = competition_components(rows)
    assert components["score"] == pytest.approx(1.0)


def test_false_positive_control_is_penalized() -> None:
    rows = [
        AreaResult("event", "rain_flood", 1_000, 100, 300, 400, 100, 300, 400),
        AreaResult("baseline", "baseline", 1_000, 5, 300, 300, 0, 300, 300),
    ]
    components = competition_components(rows)
    assert components["spec_base"] == pytest.approx(0.0)
    assert components["score"] == pytest.approx(0.85)
