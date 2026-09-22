from __future__ import annotations

import numpy as np

from hydrowatch.calibrate import threshold_statistics


def test_threshold_statistics_prefers_separating_threshold() -> None:
    probability = np.asarray([[0.05, 0.15, 0.85, 0.95]], dtype=np.float32)
    target = np.asarray([[0, 0, 1, 1]], dtype=np.uint8)
    thresholds = np.asarray([0.1, 0.5, 0.9])
    combined, dice, area = threshold_statistics(
        probability,
        target,
        thresholds,
        pixel_area_ha=1.0,
        area_floor_ha=1.0,
    )
    assert int(np.argmax(combined)) == 1
    assert dice[1] == 1.0
    assert area[1] == 1.0
