from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import torch
import torch.nn.functional as F
from torch import Tensor, nn
from torchvision.models import resnet18


def _group_count(channels: int) -> int:
    for groups in (8, 4, 2, 1):
        if channels % groups == 0:
            return groups
    return 1


class ConvNormAct(nn.Sequential):
    def __init__(self, in_channels: int, out_channels: int, kernel_size: int = 3) -> None:
        padding = kernel_size // 2
        super().__init__(
            nn.Conv2d(in_channels, out_channels, kernel_size, padding=padding, bias=False),
            nn.GroupNorm(_group_count(out_channels), out_channels),
            nn.SiLU(inplace=True),
        )


class ResNet18Encoder(nn.Module):
    channels = (64, 64, 128, 256, 512)

    def __init__(self, in_channels: int) -> None:
        super().__init__()
        backbone = resnet18(weights=None)
        backbone.conv1 = nn.Conv2d(in_channels, 64, kernel_size=7, stride=2, padding=3, bias=False)
        self.stem = nn.Sequential(backbone.conv1, backbone.bn1, backbone.relu)
        self.pool = backbone.maxpool
        self.layer1 = backbone.layer1
        self.layer2 = backbone.layer2
        self.layer3 = backbone.layer3
        self.layer4 = backbone.layer4

    def forward(self, inputs: Tensor) -> list[Tensor]:
        level1 = self.stem(inputs)
        level2 = self.layer1(self.pool(level1))
        level3 = self.layer2(level2)
        level4 = self.layer3(level3)
        level5 = self.layer4(level4)
        return [level1, level2, level3, level4, level5]


class TemporalModalFusion(nn.Module):
    def __init__(self, channels: int, aux_channels: int) -> None:
        super().__init__()
        self.sar_temporal = ConvNormAct(channels * 3, channels, kernel_size=1)
        self.optical_temporal = ConvNormAct(channels * 3, channels, kernel_size=1)
        aux_width = max(8, channels // 4)
        self.aux_projection = ConvNormAct(aux_channels, aux_width, kernel_size=1)
        self.fusion = nn.Sequential(
            ConvNormAct(channels * 2 + aux_width + 2, channels),
            ConvNormAct(channels, channels),
        )

    def forward(
        self,
        sar_pre: Tensor,
        sar_peak: Tensor,
        optical_pre: Tensor,
        optical_peak: Tensor,
        aux: Tensor,
        optical_available: Tensor,
    ) -> Tensor:
        batch = sar_pre.shape[0]
        if optical_available.shape != (batch, 2):
            raise ValueError(
                f"optical_available must be [B, 2], got {tuple(optical_available.shape)}"
            )
        pre_gate = optical_available[:, 0, None, None, None]
        peak_gate = optical_available[:, 1, None, None, None]
        optical_pre = optical_pre * pre_gate
        optical_peak = optical_peak * peak_gate

        sar = self.sar_temporal(
            torch.cat((sar_pre, sar_peak, torch.abs(sar_peak - sar_pre)), dim=1)
        )
        optical = self.optical_temporal(
            torch.cat((optical_pre, optical_peak, torch.abs(optical_peak - optical_pre)), dim=1)
        )
        any_optical = optical_available.amax(dim=1)[:, None, None, None]
        optical = optical * any_optical

        spatial_size = sar.shape[-2:]
        aux = F.interpolate(aux, size=spatial_size, mode="bilinear", align_corners=False)
        aux = self.aux_projection(aux)
        availability = optical_available[:, :, None, None].expand(-1, -1, *spatial_size)
        return self.fusion(torch.cat((sar, optical, aux, availability), dim=1))


class DecoderBlock(nn.Module):
    def __init__(self, in_channels: int, skip_channels: int, out_channels: int) -> None:
        super().__init__()
        self.block = nn.Sequential(
            ConvNormAct(in_channels + skip_channels, out_channels),
            ConvNormAct(out_channels, out_channels),
        )

    def forward(self, inputs: Tensor, skip: Tensor) -> Tensor:
        inputs = F.interpolate(inputs, size=skip.shape[-2:], mode="bilinear", align_corners=False)
        return self.block(torch.cat((inputs, skip), dim=1))


class SiameseHydroUNet(nn.Module):
    """Two temporal, two modality segmentation network with hydrologic context."""

    output_names = ("flood", "water_pre", "water_peak")

    def __init__(self, sar_channels: int = 2, optical_channels: int = 4, aux_channels: int = 6):
        super().__init__()
        self.sar_channels = sar_channels
        self.optical_channels = optical_channels
        self.aux_channels = aux_channels
        self.sar_encoder = ResNet18Encoder(sar_channels)
        self.optical_encoder = ResNet18Encoder(optical_channels)
        channels: Sequence[int] = self.sar_encoder.channels
        self.fusions = nn.ModuleList(
            [TemporalModalFusion(width, aux_channels) for width in channels]
        )
        self.decode4 = DecoderBlock(512, 256, 256)
        self.decode3 = DecoderBlock(256, 128, 128)
        self.decode2 = DecoderBlock(128, 64, 64)
        self.decode1 = DecoderBlock(64, 64, 64)
        self.final = nn.Sequential(
            ConvNormAct(64, 32),
            nn.Conv2d(32, len(self.output_names), kernel_size=1),
        )

    @staticmethod
    def _encode_pair(encoder: nn.Module, inputs: Tensor) -> tuple[list[Tensor], list[Tensor]]:
        if inputs.ndim != 5 or inputs.shape[1] != 2:
            raise ValueError(f"temporal input must be [B, 2, C, H, W], got {tuple(inputs.shape)}")
        batch = inputs.shape[0]
        stacked = torch.cat((inputs[:, 0], inputs[:, 1]), dim=0)
        features = encoder(stacked)
        pre = [feature[:batch] for feature in features]
        peak = [feature[batch:] for feature in features]
        return pre, peak

    def load_encoder_checkpoint(self, modality: str, checkpoint: str | Path) -> None:
        """Load a local encoder state dict; never downloads weights implicitly."""
        encoder = {"sar": self.sar_encoder, "optical": self.optical_encoder}.get(modality)
        if encoder is None:
            raise ValueError("modality must be 'sar' or 'optical'")
        payload = torch.load(Path(checkpoint), map_location="cpu", weights_only=True)
        state = payload.get("state_dict", payload) if isinstance(payload, dict) else payload
        cleaned = {}
        for key, value in state.items():
            for prefix in ("module.", "model.", "encoder.", "backbone."):
                if key.startswith(prefix):
                    key = key[len(prefix) :]
            if key.startswith("fc."):
                continue
            if key == "conv1.weight":
                key = "stem.0.weight"
                expected_channels = encoder.stem[0].in_channels
                if value.shape[1] == 13 and expected_channels == 4:
                    # SSL4EO-S12 order: B1,B2,B3,B4,B5,B6,B7,B8,B8a,B9,B10,B11,B12.
                    value = value[:, [2, 3, 7, 11]]
                elif value.shape[1] != expected_channels:
                    raise ValueError(
                        f"cannot adapt {modality} conv1 from {value.shape[1]} "
                        f"to {expected_channels} channels"
                    )
            elif key.startswith("bn1."):
                key = "stem.1." + key.removeprefix("bn1.")
            cleaned[key] = value
        missing, unexpected = encoder.load_state_dict(cleaned, strict=False)
        if unexpected:
            raise ValueError(f"unexpected {modality} checkpoint keys: {unexpected[:8]}")
        if len(missing) > 4:
            raise ValueError(f"too many missing {modality} checkpoint keys: {missing[:8]}")

    def forward(
        self,
        sar: Tensor,
        optical: Tensor,
        aux: Tensor,
        optical_available: Tensor,
    ) -> Tensor:
        if sar.shape[2] != self.sar_channels:
            raise ValueError(f"expected {self.sar_channels} SAR channels, got {sar.shape[2]}")
        if optical.shape[2] != self.optical_channels:
            raise ValueError(
                f"expected {self.optical_channels} optical channels, got {optical.shape[2]}"
            )
        if aux.ndim != 4 or aux.shape[1] != self.aux_channels:
            raise ValueError(f"aux must be [B, {self.aux_channels}, H, W], got {tuple(aux.shape)}")
        if sar.shape[0] != optical.shape[0] or sar.shape[0] != aux.shape[0]:
            raise ValueError("all inputs must have the same batch size")
        input_size = sar.shape[-2:]
        if optical.shape[-2:] != input_size or aux.shape[-2:] != input_size:
            raise ValueError("all raster inputs must share the same spatial size")

        sar_pre, sar_peak = self._encode_pair(self.sar_encoder, sar)
        optical_pre, optical_peak = self._encode_pair(self.optical_encoder, optical)
        fused = [
            fusion(s_pre, s_peak, o_pre, o_peak, aux, optical_available)
            for fusion, s_pre, s_peak, o_pre, o_peak in zip(
                self.fusions, sar_pre, sar_peak, optical_pre, optical_peak, strict=True
            )
        ]
        level1, level2, level3, level4, level5 = fused
        decoded = self.decode4(level5, level4)
        decoded = self.decode3(decoded, level3)
        decoded = self.decode2(decoded, level2)
        decoded = self.decode1(decoded, level1)
        decoded = F.interpolate(decoded, size=input_size, mode="bilinear", align_corners=False)
        return self.final(decoded)
