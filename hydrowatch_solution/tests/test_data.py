from __future__ import annotations

import numpy as np

from hydrowatch.data import (
    GeoPatchDataset,
    load_pairs,
    normalize_aux,
    normalize_optical,
    normalize_sar,
)
from hydrowatch.validate import validate_dataset


def test_normalizers_are_finite_and_bounded() -> None:
    sar = normalize_sar(np.asarray([[[-100.0, np.nan, 20.0]]], dtype=np.float32))
    optical = normalize_optical(np.asarray([[[-1.0, np.nan, 20_000.0]]], dtype=np.float32))
    aux = normalize_aux(np.ones((6, 2, 2), dtype=np.float32) * 1_000)
    assert np.isfinite(sar).all() and np.max(np.abs(sar)) <= 1.0
    assert np.isfinite(optical).all() and optical.min() >= 0 and optical.max() <= 1
    assert np.isfinite(aux).all() and aux.min() >= 0 and aux.max() <= 1


def test_validator_and_patch_dataset(tiny_dataset) -> None:
    report = validate_dataset(tiny_dataset, require_scenes=True)
    assert report.ok, report.errors
    records = load_pairs(tiny_dataset)
    dataset = GeoPatchDataset(
        records,
        patch_size=16,
        samples_per_epoch=4,
        positive_fraction=1.0,
        optical_dropout=0.0,
        augment=True,
    )
    sample = dataset[0]
    assert sample["sar"].shape == (2, 2, 16, 16)
    assert sample["optical"].shape == (2, 4, 16, 16)
    assert sample["aux"].shape == (6, 16, 16)
    assert sample["target"].shape == (5, 16, 16)
    assert sample["valid"].shape == (1, 16, 16)
    assert sample["target"][0].sum() > 0


def test_modality_dropout_removes_optical(tiny_dataset) -> None:
    dataset = GeoPatchDataset(
        load_pairs(tiny_dataset),
        patch_size=16,
        samples_per_epoch=1,
        positive_fraction=1.0,
        optical_dropout=1.0,
        augment=False,
    )
    sample = dataset[0]
    assert sample["optical"].count_nonzero() == 0
    assert sample["optical_available"].tolist() == [0.0, 0.0]
