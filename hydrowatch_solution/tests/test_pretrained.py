from __future__ import annotations

from pathlib import Path

import torch

from hydrowatch.model import SiameseHydroUNet


def test_ssl4eo_checkpoint_channel_selection(tmp_path: Path) -> None:
    model = SiameseHydroUNet()
    original = model.optical_encoder.state_dict()
    payload = {key: value.clone() for key, value in original.items()}
    payload = {
        key.replace("stem.0.", "conv1.").replace("stem.1.", "bn1."): value
        for key, value in payload.items()
    }
    source_conv = torch.arange(64 * 13 * 7 * 7, dtype=torch.float32).reshape(64, 13, 7, 7)
    payload["conv1.weight"] = source_conv
    checkpoint = tmp_path / "ssl4eo.pth"
    torch.save(payload, checkpoint)

    model.load_encoder_checkpoint("optical", checkpoint)

    assert torch.equal(model.optical_encoder.stem[0].weight, source_conv[:, [2, 3, 7, 11]])
