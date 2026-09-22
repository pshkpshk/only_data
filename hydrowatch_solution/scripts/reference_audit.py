#!/usr/bin/env python3
"""Аудит эталонных масок и расчёт официальной метрики по submission.csv.

Не требует снимков Sentinel: читает только reference_masks/*.tif|json, pairs.csv и
submission.csv. Результат — markdown-таблицы для отчёта (раздел «Критический анализ
эталона и метрики») и JSON с числами.

    python scripts/reference_audit.py --root data/raw/hydrowatch_amur \
        --submission predictions/submission.csv --output outputs/reference_audit
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
import rasterio

BANDS = ("flood", "water_pre", "water_peak", "permanent", "receded")


def audit_pair(mask_path: Path) -> dict[str, float]:
    with rasterio.open(mask_path) as dataset:
        px_ha = abs(dataset.transform.a * dataset.transform.e) / 10_000.0
        data = dataset.read().astype(bool)
    flood, pre, peak, perm, receded = data
    formula = peak & ~pre & ~perm
    ha = lambda a: float(np.count_nonzero(a) * px_ha)  # noqa: E731
    return {
        "aoi_ha": float(flood.size * px_ha),
        "flood_ha": ha(flood),
        "flood_formula_ha": ha(formula),
        "flood_xor_formula_ha": ha(flood ^ formula),
        "flood_and_pre_ha": ha(flood & pre),
        "flood_and_permanent_ha": ha(flood & perm),
        "flood_not_peak_ha": ha(flood & ~peak),
        "water_pre_ha": ha(pre),
        "water_peak_ha": ha(peak),
        "permanent_ha": ha(perm),
        "permanent_missing_in_pre_ha": ha(perm & ~pre),
        "permanent_missing_in_peak_ha": ha(perm & ~peak),
        "receded_ha": ha(receded),
    }


def q(x: float, ref: float, floor: float) -> float:
    return max(0.0, 1.0 - abs(x - ref) / max(ref, floor))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--root", type=Path, default=Path("data/raw/hydrowatch_amur"))
    parser.add_argument("--submission", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=Path("outputs/reference_audit"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    with (args.root / "pairs.csv").open(encoding="utf-8-sig", newline="") as stream:
        pairs = list(csv.DictReader(stream))
    audit: dict[str, dict] = {}
    for row in pairs:
        mask_path = args.root / row["reference_mask"]
        meta = json.loads(mask_path.with_suffix(".json").read_text(encoding="utf-8"))
        stats = audit_pair(mask_path)
        stats["json_flood_ha"] = meta["stats"]["flood_ha"]
        stats["json_water_pre_ha"] = meta["stats"]["water_pre_ha"]
        stats["json_water_peak_ha"] = meta["stats"]["water_peak_ha"]
        stats["reference_source"] = meta.get("reference_source", "")
        stats["event_kind"] = row["event_kind"]
        stats["has_optical"] = bool(row.get("sensor_optical", "").strip())
        stats["relative_orbit"] = row.get("relative_orbit", "")
        audit[row["pair_id"]] = stats

    lines = ["## Аудит эталонных масок", ""]
    lines.append(
        "| pair | источник | орбита | оптика | flood, га | peak&~pre&~perm, га | расхожд. с формулой, га | pre, га | peak, га | perm, га | perm вне pre, га | perm вне peak, га | JSON flood, га |"
    )
    lines.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for pid, s in audit.items():
        lines.append(
            f"| {pid} | {s['reference_source']} | {s['relative_orbit']} | {'да' if s['has_optical'] else 'нет'} | {s['flood_ha']:.0f} | {s['flood_formula_ha']:.0f} | {s['flood_xor_formula_ha']:.0f} | {s['water_pre_ha']:.0f} | {s['water_peak_ha']:.0f} | {s['permanent_ha']:.0f} | {s['permanent_missing_in_pre_ha']:.0f} | {s['permanent_missing_in_peak_ha']:.0f} | {s['json_flood_ha']:.0f} |"
        )
    lines.append("")
    lines.append(
        "Признаки проблемной пары: «perm вне pre/peak» сопоставимо с perm (маска воды почти не содержит постоянной воды → сцена покрывала AOI частично), JSON flood ≠ flood по маске, большое расхождение flood с формулой peak&~pre&~perm."
    )

    if args.submission and args.submission.is_file():
        with args.submission.open(encoding="utf-8-sig", newline="") as stream:
            sub = {r["pair_id"]: r for r in csv.DictReader(stream)}
        qf, qpk, qpre, spec = [], [], [], []
        lines += [
            "",
            "## Официальная метрика по submission.csv (self-check на выданном эталоне)",
            "",
        ]
        lines.append(
            "| pair | flood ref/sub | q_flood | pre ref/sub | q_pre | peak ref/sub | q_peak |"
        )
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for pid, s in audit.items():
            r = sub.get(pid)
            if r is None:
                continue
            f, p, k = (float(r["flood_ha"]), float(r["water_pre_ha"]), float(r["water_peak_ha"]))
            if s["event_kind"] == "baseline":
                share = max(0.0, f - s["json_flood_ha"]) / s["aoi_ha"]
                sp = 1.0 - min(1.0, share / 0.005)
                spec.append(sp)
                lines.append(
                    f"| {pid} (межень) | {s['json_flood_ha']:.0f}/{f:.0f} | spec={sp:.2f} | {s['json_water_pre_ha']:.0f}/{p:.0f} | – | {s['json_water_peak_ha']:.0f}/{k:.0f} | – |"
                )
            else:
                a, b, c = (
                    q(f, s["json_flood_ha"], 50),
                    q(p, s["json_water_pre_ha"], 200),
                    q(k, s["json_water_peak_ha"], 200),
                )
                qf.append(a)
                qpre.append(b)
                qpk.append(c)
                lines.append(
                    f"| {pid} | {s['json_flood_ha']:.0f}/{f:.0f} | {a:.2f} | {s['json_water_pre_ha']:.0f}/{p:.0f} | {b:.2f} | {s['json_water_peak_ha']:.0f}/{k:.0f} | {c:.2f} |"
                )
        comp = {
            "q_flood": float(np.mean(qf)),
            "q_water_peak": float(np.mean(qpk)),
            "q_water_pre": float(np.mean(qpre)),
            "spec_base": float(np.mean(spec)) if spec else None,
        }
        comp["score"] = (
            0.45 * comp["q_flood"]
            + 0.25 * comp["q_water_peak"]
            + 0.15 * comp["q_water_pre"]
            + 0.15 * (comp["spec_base"] or 0.0)
        )
        lines.append("")
        lines.append(
            f"**Score = {comp['score']:.4f}** (Q_flood={comp['q_flood']:.4f}, Q_peak={comp['q_water_peak']:.4f}, Q_pre={comp['q_water_pre']:.4f}, Spec_base={comp['spec_base']:.4f}) — по JSON-статистике эталона."
        )
        (args.output / "metric_components.json").write_text(
            json.dumps(comp, indent=2), encoding="utf-8"
        )

    (args.output / "reference_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output / "reference_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
