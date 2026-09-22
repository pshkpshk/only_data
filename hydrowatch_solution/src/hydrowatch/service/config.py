from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Land-cover proxy classes derived from AUX_terrain_gsw.tif (WorldCover built-up +
# JRC Global Surface Water statistics + MERIT HAND). Order matters: first match wins.
DEFAULT_LANDCOVER_CLASSES = [
    {"code": "builtup", "name": "Застройка (ESA WorldCover built-up)"},
    {"code": "permanent_water", "name": "Постоянная вода (GSW occurrence ≥ порога)"},
    {"code": "seasonal_water", "name": "Сезонная вода (GSW seasonality ≥ 1 мес)"},
    {"code": "floodplain", "name": "Пойма многолетнего разлива (GSW max_extent)"},
    {"code": "lowland", "name": "Низкие земли вне разлива (HAND < 5 м)"},
    {"code": "upland", "name": "Возвышенные земли (HAND ≥ 5 м)"},
]


@dataclass
class ServiceConfig:
    predictions_dir: Path = Path("predictions")
    data_root: Path | None = Path("data/raw/hydrowatch_amur")
    pairs_csv: Path | None = None
    state_dir: Path = Path("outputs/service_state")
    date_tolerance_days: int = 20
    min_mapping_unit_ha: float = 0.25
    simplify_tolerance_m: float = 5.0
    overlay_max_size: int = 1500
    permanent_occurrence_pct: float = 80.0
    lowland_hand_m: float = 5.0
    landcover_classes: list[dict[str, str]] = field(
        default_factory=lambda: [dict(item) for item in DEFAULT_LANDCOVER_CLASSES]
    )
    model_name: str = "Siamese multimodal U-Net (ResNet-18 x2), thresholds 0.95/0.78/0.95"

    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None) -> ServiceConfig:
        """Load YAML config, then apply environment overrides.

        Precedence: environment variables > YAML file > defaults. All paths are resolved
        relative to the current working directory (never machine-specific).
        """
        config = cls()
        config_path = Path(
            path or os.environ.get("HYDROWATCH_SERVICE_CONFIG", "configs/service.yaml")
        )
        if config_path.is_file():
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            for key, value in payload.items():
                if not hasattr(config, key):
                    raise ValueError(f"unknown service config key: {key}")
                if key in {"predictions_dir", "data_root", "pairs_csv", "state_dir"}:
                    value = Path(value) if value not in (None, "") else None
                setattr(config, key, value)

        env_predictions = os.environ.get("HYDROWATCH_PREDICTIONS_DIR")
        if env_predictions:
            config.predictions_dir = Path(env_predictions)
        env_root = os.environ.get("HYDROWATCH_DATA_ROOT")
        if env_root is not None:
            config.data_root = Path(env_root) if env_root else None
        env_pairs = os.environ.get("HYDROWATCH_PAIRS_CSV")
        if env_pairs:
            config.pairs_csv = Path(env_pairs)
        env_state = os.environ.get("HYDROWATCH_STATE_DIR")
        if env_state:
            config.state_dir = Path(env_state)
        return config

    def resolve_pairs_csv(self) -> Path:
        candidates = []
        if self.pairs_csv is not None:
            candidates.append(self.pairs_csv)
        if self.data_root is not None:
            candidates.append(self.data_root / "pairs.csv")
        candidates.append(self.predictions_dir / "pairs.csv")
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        raise FileNotFoundError(
            "pairs.csv not found; set HYDROWATCH_DATA_ROOT, HYDROWATCH_PAIRS_CSV or copy "
            f"pairs.csv next to the masks. Tried: {[str(c) for c in candidates]}"
        )
