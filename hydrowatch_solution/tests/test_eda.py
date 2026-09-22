from __future__ import annotations

import pytest

from hydrowatch.eda import build_aux_label_profile, build_pair_profile


def test_pair_profile_recomputes_mask_areas(tiny_dataset) -> None:
    profile = build_pair_profile(tiny_dataset)
    assert len(profile) == 1
    assert profile.loc[0, "flood_ha_mask"] == pytest.approx(0.20)
    assert profile.loc[0, "flood_not_peak_px"] == 0
    assert profile.loc[0, "aux_aligned"]


def test_aux_profile_contains_both_classes(tiny_dataset) -> None:
    profile = build_aux_label_profile(tiny_dataset)
    assert set(profile["feature"]) == {"slope", "hand", "occurrence"}
    assert set(profile["label"]) == {"flood", "background"}
