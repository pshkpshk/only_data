from __future__ import annotations

import torch
import torch.nn.functional as F
from torch import Tensor, nn


def _expand_valid(valid: Tensor | None, like: Tensor) -> Tensor:
    if valid is None:
        return torch.ones_like(like)
    if valid.ndim != 4 or valid.shape[1] != 1:
        raise ValueError(f"valid must be [B, 1, H, W], got {tuple(valid.shape)}")
    return valid.expand_as(like).to(dtype=like.dtype)


def masked_bce_with_logits(logits: Tensor, target: Tensor, valid: Tensor | None) -> Tensor:
    weights = _expand_valid(valid, logits)
    loss = F.binary_cross_entropy_with_logits(logits, target, reduction="none")
    return (loss * weights).sum() / weights.sum().clamp_min(1.0)


def dice_loss(logits: Tensor, target: Tensor, valid: Tensor | None, eps: float = 1e-6) -> Tensor:
    probabilities = logits.sigmoid()
    weights = _expand_valid(valid, probabilities)
    dims = (2, 3)
    intersection = (probabilities * target * weights).sum(dim=dims)
    denominator = ((probabilities + target) * weights).sum(dim=dims)
    score = (2.0 * intersection + eps) / (denominator + eps)
    return 1.0 - score.mean()


def focal_tversky_loss(
    logits: Tensor,
    target: Tensor,
    valid: Tensor | None,
    alpha: float = 0.3,
    beta: float = 0.7,
    gamma: float = 0.75,
    eps: float = 1e-6,
) -> Tensor:
    probabilities = logits.sigmoid()
    weights = _expand_valid(valid, probabilities)
    dims = (2, 3)
    true_positive = (probabilities * target * weights).sum(dim=dims)
    false_positive = (probabilities * (1.0 - target) * weights).sum(dim=dims)
    false_negative = ((1.0 - probabilities) * target * weights).sum(dim=dims)
    tversky = (true_positive + eps) / (
        true_positive + alpha * false_positive + beta * false_negative + eps
    )
    return torch.pow(1.0 - tversky, gamma).mean()


def area_fraction_loss(
    logits: Tensor,
    target: Tensor,
    valid: Tensor | None,
    minimum_fraction: float = 5e-4,
) -> Tensor:
    probabilities = logits.sigmoid()
    weights = _expand_valid(valid, probabilities)
    valid_pixels = weights.sum(dim=(2, 3)).clamp_min(1.0)
    predicted_fraction = (probabilities * weights).sum(dim=(2, 3)) / valid_pixels
    target_fraction = (target * weights).sum(dim=(2, 3)) / valid_pixels
    denominator = target_fraction.clamp_min(minimum_fraction)
    return (torch.abs(predicted_fraction - target_fraction) / denominator).clamp_max(5.0).mean()


class HydroLoss(nn.Module):
    """Pixel, area, and logical consistency objectives aligned with the competition."""

    def __init__(self) -> None:
        super().__init__()

    def forward(
        self, logits: Tensor, target: Tensor, valid: Tensor | None = None
    ) -> tuple[Tensor, dict[str, Tensor]]:
        if logits.ndim != 4 or logits.shape[1] != 3:
            raise ValueError(f"logits must be [B, 3, H, W], got {tuple(logits.shape)}")
        if target.ndim != 4 or target.shape[1] < 4:
            raise ValueError(
                f"target must contain at least 4 mask bands, got {tuple(target.shape)}"
            )
        if target.shape[0] != logits.shape[0] or target.shape[-2:] != logits.shape[-2:]:
            raise ValueError("logits and target shapes are incompatible")

        flood_logits = logits[:, 0:1]
        water_pre_logits = logits[:, 1:2]
        water_peak_logits = logits[:, 2:3]
        flood = target[:, 0:1]
        water_pre = target[:, 1:2]
        water_peak = target[:, 2:3]
        permanent = target[:, 3:4]

        flood_loss = focal_tversky_loss(flood_logits, flood, valid)
        pre_loss = 0.5 * dice_loss(
            water_pre_logits, water_pre, valid
        ) + 0.5 * masked_bce_with_logits(water_pre_logits, water_pre, valid)
        peak_loss = 0.5 * dice_loss(
            water_peak_logits, water_peak, valid
        ) + 0.5 * masked_bce_with_logits(water_peak_logits, water_peak, valid)
        area_loss = area_fraction_loss(logits, target[:, :3], valid)

        probabilities = logits.sigmoid()
        flood_probability = probabilities[:, 0:1]
        valid_weights = _expand_valid(valid, flood_probability)
        consistency_map = (
            F.relu(flood_probability - probabilities[:, 2:3])
            + flood_probability * target[:, 1:2]
            + flood_probability * permanent
        )
        consistency_loss = (consistency_map * valid_weights).sum() / valid_weights.sum().clamp_min(
            1.0
        )

        components = {
            "flood": flood_loss,
            "water_peak": peak_loss,
            "water_pre": pre_loss,
            "area": area_loss,
            "consistency": consistency_loss,
        }
        total = (
            0.45 * flood_loss
            + 0.25 * peak_loss
            + 0.15 * pre_loss
            + 0.10 * area_loss
            + 0.05 * consistency_loss
        )
        return total, components
