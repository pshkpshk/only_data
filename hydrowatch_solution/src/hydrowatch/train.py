from __future__ import annotations

import argparse
import json
import os
import random
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import numpy as np
import torch
import yaml
from torch.utils.data import DataLoader

from hydrowatch.data import GeoPatchDataset, load_pairs, split_by_event
from hydrowatch.losses import HydroLoss
from hydrowatch.model import SiameseHydroUNet
from hydrowatch.validate import validate_dataset


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(False)
    torch.backends.cudnn.benchmark = True


def load_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream)
    required = {"seed", "data_root", "output_dir", "data", "model", "training"}
    missing = required - set(config)
    if missing:
        raise ValueError(f"config is missing sections: {sorted(missing)}")
    return config


def select_device(requested: str) -> torch.device:
    if requested != "auto":
        device = torch.device(requested)
    elif torch.cuda.is_available():
        device = torch.device("cuda")
    elif torch.backends.mps.is_available():
        device = torch.device("mps")
    else:
        device = torch.device("cpu")
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable")
    return device


def _move_batch(batch: dict[str, object], device: torch.device) -> dict[str, object]:
    return {
        key: value.to(device, non_blocking=True) if isinstance(value, torch.Tensor) else value
        for key, value in batch.items()
    }


def _autocast(device: torch.device, enabled: bool):
    if enabled and device.type == "cuda":
        return torch.autocast(device_type="cuda", dtype=torch.float16)
    return nullcontext()


def run_epoch(
    model: SiameseHydroUNet,
    loader: DataLoader,
    criterion: HydroLoss,
    device: torch.device,
    *,
    optimizer: torch.optim.Optimizer | None,
    scaler: torch.amp.GradScaler | None,
    accumulation_steps: int,
    amp: bool,
) -> dict[str, float]:
    training = optimizer is not None
    model.train(training)
    if training:
        optimizer.zero_grad(set_to_none=True)
    metric_names = ("loss", "flood", "water_peak", "water_pre", "area", "consistency")
    totals: dict[str, float] = {name: 0.0 for name in metric_names}
    batches = 0

    for batch_index, raw_batch in enumerate(loader):
        batch = _move_batch(raw_batch, device)
        with torch.set_grad_enabled(training), _autocast(device, amp):
            logits = model(
                batch["sar"],
                batch["optical"],
                batch["aux"],
                batch["optical_available"],
            )
            loss, components = criterion(logits, batch["target"], batch["valid"])
            scaled_loss = loss / accumulation_steps

        if training:
            if scaler is not None:
                scaler.scale(scaled_loss).backward()
            else:
                scaled_loss.backward()
            should_step = (batch_index + 1) % accumulation_steps == 0 or batch_index + 1 == len(
                loader
            )
            if should_step:
                if scaler is not None:
                    scaler.unscale_(optimizer)
                    torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=5.0)
                    scaler.step(optimizer)
                    scaler.update()
                else:
                    torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=5.0)
                    optimizer.step()
                optimizer.zero_grad(set_to_none=True)

        totals["loss"] += float(loss.detach())
        for name, value in components.items():
            totals[name] += float(value.detach())
        batches += 1

    if batches == 0:
        raise RuntimeError("data loader produced no batches")
    return {name: value / batches for name, value in totals.items()}


def save_checkpoint(
    path: Path,
    *,
    model: SiameseHydroUNet,
    optimizer: torch.optim.Optimizer,
    epoch: int,
    validation_loss: float,
    config: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    torch.save(
        {
            "epoch": epoch,
            "validation_loss": validation_loss,
            "model": model.state_dict(),
            "optimizer": optimizer.state_dict(),
            "config": config,
        },
        temporary,
    )
    os.replace(temporary, path)


def train(config_path: str | Path, *, device_name: str = "auto") -> Path:
    config = load_config(config_path)
    seed = int(config["seed"])
    seed_everything(seed)
    data_root = Path(config["data_root"]).expanduser().resolve()
    report = validate_dataset(data_root, require_scenes=True)
    if not report.ok:
        preview = "\n".join(f"- {error}" for error in report.errors[:12])
        remainder = len(report.errors) - min(12, len(report.errors))
        suffix = f"\n- ... and {remainder} more" if remainder else ""
        raise RuntimeError(f"dataset is not trainable:\n{preview}{suffix}")

    records = load_pairs(data_root)
    training_config = config["training"]
    data_config = config["data"]
    train_records, validation_records = split_by_event(
        records, str(training_config["validation_event"])
    )
    common_dataset = {
        "patch_size": int(data_config["patch_size"]),
        "positive_fraction": float(data_config["positive_fraction"]),
        "seed": seed,
    }
    train_dataset = GeoPatchDataset(
        train_records,
        samples_per_epoch=int(data_config["samples_per_epoch"]),
        optical_dropout=float(data_config["optical_dropout"]),
        augment=True,
        **common_dataset,
    )
    validation_dataset = GeoPatchDataset(
        validation_records,
        samples_per_epoch=max(128, int(data_config["samples_per_epoch"]) // 8),
        optical_dropout=0.0,
        augment=False,
        **common_dataset,
    )
    batch_size = int(training_config["batch_size"])
    workers = int(data_config["num_workers"])
    loader_options = {
        "batch_size": batch_size,
        "num_workers": workers,
        "pin_memory": True,
        "persistent_workers": workers > 0,
    }
    train_loader = DataLoader(train_dataset, shuffle=False, drop_last=True, **loader_options)
    validation_loader = DataLoader(validation_dataset, shuffle=False, **loader_options)

    device = select_device(device_name)
    model_config = config["model"]
    model = SiameseHydroUNet(
        sar_channels=int(model_config["sar_channels"]),
        optical_channels=int(model_config["optical_channels"]),
        aux_channels=int(model_config["aux_channels"]),
    )
    initial_checkpoint = training_config.get("initial_checkpoint")
    if initial_checkpoint:
        initial_payload = torch.load(
            Path(initial_checkpoint).expanduser().resolve(),
            map_location="cpu",
            weights_only=False,
        )
        model.load_state_dict(initial_payload["model"], strict=True)
    else:
        for modality, key in (("sar", "pretrained_sar"), ("optical", "pretrained_optical")):
            if model_config.get(key):
                model.load_encoder_checkpoint(modality, model_config[key])
    model.to(device)
    criterion = HydroLoss()
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=float(training_config["learning_rate"]),
        weight_decay=float(training_config["weight_decay"]),
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=int(training_config["epochs"])
    )
    amp = bool(training_config["amp"]) and device.type == "cuda"
    scaler = torch.amp.GradScaler("cuda", enabled=True) if amp else None
    output_dir = Path(config["output_dir"]).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "metrics.jsonl"
    best_loss = float("inf")
    stale_epochs = 0
    patience = int(training_config["early_stopping_patience"])

    for epoch in range(int(training_config["epochs"])):
        train_dataset.set_epoch(epoch)
        validation_dataset.set_epoch(epoch)
        train_metrics = run_epoch(
            model,
            train_loader,
            criterion,
            device,
            optimizer=optimizer,
            scaler=scaler,
            accumulation_steps=int(training_config["accumulation_steps"]),
            amp=amp,
        )
        validation_metrics = run_epoch(
            model,
            validation_loader,
            criterion,
            device,
            optimizer=None,
            scaler=None,
            accumulation_steps=1,
            amp=amp,
        )
        scheduler.step()
        record = {
            "epoch": epoch,
            "learning_rate": optimizer.param_groups[0]["lr"],
            "train": train_metrics,
            "validation": validation_metrics,
        }
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        save_checkpoint(
            output_dir / "last.pt",
            model=model,
            optimizer=optimizer,
            epoch=epoch,
            validation_loss=validation_metrics["loss"],
            config=config,
        )
        if validation_metrics["loss"] < best_loss:
            best_loss = validation_metrics["loss"]
            stale_epochs = 0
            save_checkpoint(
                output_dir / "best.pt",
                model=model,
                optimizer=optimizer,
                epoch=epoch,
                validation_loss=best_loss,
                config=config,
            )
        else:
            stale_epochs += 1
            if stale_epochs >= patience:
                break
    return output_dir / "best.pt"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train the HydroWatch segmentation model")
    parser.add_argument("--config", type=Path, default=Path("configs/train.yaml"))
    parser.add_argument("--device", default="auto", help="auto, cuda, mps, or cpu")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    checkpoint = train(args.config, device_name=args.device)
    print(f"best checkpoint: {checkpoint}")


if __name__ == "__main__":
    main()
