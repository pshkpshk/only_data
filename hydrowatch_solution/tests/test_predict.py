from __future__ import annotations

import numpy as np

from hydrowatch.predict import (
    apply_pair_rules,
    blend_weights,
    postprocess_masks,
    sliding_positions,
)


def test_sliding_positions_cover_tail_without_duplicates() -> None:
    assert sliding_positions(1000, 384, 256) == [0, 256, 512, 616]
    assert sliding_positions(384, 384, 256) == [0]


def test_blend_weights_are_positive() -> None:
    weights = blend_weights(32)
    assert weights.shape == (1, 32, 32)
    assert np.all(weights > 0)


def test_postprocess_enforces_hydrologic_constraints() -> None:
    probabilities = np.zeros((3, 2, 2), dtype=np.float32)
    probabilities[0, 0, :] = 0.9
    probabilities[1, 0, 0] = 0.9
    masks = postprocess_masks(probabilities, (0.5, 0.5, 0.5))
    flood, water_pre, water_peak = masks
    assert flood[0, 0] == 0
    assert flood[0, 1] == 1
    assert np.all(flood <= water_peak)
    assert not np.any(flood & water_pre)


def test_baseline_pair_rule_suppresses_only_flood() -> None:
    masks = np.ones((3, 2, 2), dtype=np.uint8)
    baseline = apply_pair_rules(masks, "baseline")
    event = apply_pair_rules(masks, "rain_flood")
    assert not np.any(baseline[0])
    assert np.all(baseline[1:] == 1)
    assert np.array_equal(event, masks)
    assert np.all(masks == 1)
