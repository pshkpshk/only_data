from __future__ import annotations

import torch

from hydrowatch.losses import HydroLoss
from hydrowatch.model import SiameseHydroUNet


def _inputs(batch: int = 1, size: int = 64):
    return (
        torch.randn(batch, 2, 2, size, size),
        torch.randn(batch, 2, 4, size, size),
        torch.rand(batch, 6, size, size),
        torch.tensor([[1.0, 0.0]]).expand(batch, -1),
    )


def test_model_forward_with_partial_optical() -> None:
    model = SiameseHydroUNet()
    output = model(*_inputs())
    assert output.shape == (1, 3, 64, 64)
    assert torch.isfinite(output).all()


def test_model_and_loss_backward_without_optical() -> None:
    model = SiameseHydroUNet()
    sar, optical, aux, _ = _inputs()
    logits = model(sar, optical.zero_(), aux, torch.zeros(1, 2))
    target = torch.zeros(1, 5, 64, 64)
    target[:, 0, 20:28, 20:28] = 1
    target[:, 2, 18:30, 18:30] = 1
    valid = torch.ones(1, 1, 64, 64)
    loss, components = HydroLoss()(logits, target, valid)
    loss.backward()
    assert torch.isfinite(loss)
    assert set(components) == {"flood", "water_peak", "water_pre", "area", "consistency"}
    assert any(parameter.grad is not None for parameter in model.parameters())


def test_perfect_logits_have_lower_loss() -> None:
    target = torch.zeros(1, 5, 16, 16)
    target[:, 0, 4:8, 4:8] = 1
    target[:, 2, 4:8, 4:8] = 1
    labels = target[:, :3]
    perfect = torch.where(labels > 0, torch.tensor(12.0), torch.tensor(-12.0))
    inverted = -perfect
    criterion = HydroLoss()
    perfect_loss, _ = criterion(perfect, target)
    inverted_loss, _ = criterion(inverted, target)
    assert perfect_loss < inverted_loss
