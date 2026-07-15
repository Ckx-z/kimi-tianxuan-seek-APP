"""YAML → CSV 条件补全脚本（完整版）。

两阶段补全：
1. YAML 精确匹配：paper_id + group_id → 从 structured_v2/v3 提取条件
2. 单体推断：aug_/hr_/cr_ 行用同醛/同胺的条件众数推断

输出：
  data/interim/v5_train_stage1_cond_filled.csv
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_INTERIM = PROJECT_ROOT / "data" / "interim"
DATA_INTERIM.mkdir(parents=True, exist_ok=True)

OLD_PROJECT_ROOT = Path(r"C:\Users\ckx\Desktop\tianxuan seek")
YAML_DIRS = [
    OLD_PROJECT_ROOT / "data" / "structured_v2",
    OLD_PROJECT_ROOT / "data" / "structured_v3",
]

INPUT_CSV = DATA_RAW / "v5_train_stage1.csv"
OUTPUT_CSV = DATA_INTERIM / "v5_train_stage1_cond_filled.csv"
OUTPUT_JSON = DATA_INTERIM / "condition_fill_report.json"

# YAML → CSV 字段映射
FIELD_MAP = {
    "solvent": "solvent",
    "temperature": "temperature",
    "duration": "time",
    "catalyst": "catalyst",
    "catalyst_amount": "catalyst_volume",
    "synthesis_route": "synthesis_route",
    "interface_type": "interface_type",
    "atmosphere": "atmosphere",
    "additives": "additive",
}

CSV_FIELDS = ["solvent", "temperature", "time", "catalyst", "catalyst_volume",
              "synthesis_route", "interface_type", "atmosphere", "additive"]


def _is_empty(val: str | None) -> bool:
    if val is None:
        return True
    s = str(val).strip()
    return s == "" or s.lower() in ("nan", "null", "none")


def _clean_value(val) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    if s.lower() in ("null", "none", "nan", ""):
        return None
    return s


def load_yaml_conditions() -> dict:
    """加载 YAML 条件数据。"""
    conditions = {}
    for yaml_dir in YAML_DIRS:
        if not yaml_dir.exists():
            continue
        yaml_files = list(yaml_dir.glob("*.yaml"))
        for fpath in yaml_files:
            try:
                with open(fpath, "r", encoding="utf-8") as fh:
                    data = yaml.safe_load(fh)
            except Exception:
                continue
            if not data or "experiments" not in data:
                continue
            paper_id = data.get("paper_id")
            if paper_id is None:
                continue
            try:
                paper_id = int(paper_id)
            except ValueError:
                continue
            for exp in data["experiments"]:
                group_id = exp.get("group_id")
                if group_id is None:
                    continue
                try:
                    group_id = int(group_id)
                except ValueError:
                    continue
                key = (paper_id, group_id)
                cond_data = exp.get("conditions", {})
                if not cond_data:
                    continue
                record = {}
                for yaml_field, csv_field in FIELD_MAP.items():
                    val = _clean_value(cond_data.get(yaml_field))
                    if val is not None:
                        record[csv_field] = val
                if record:
                    conditions[key] = record
    return conditions


def build_monomer_inference(rows: list) -> tuple:
    """建立单体条件推断库。"""
    ald_infer = defaultdict(lambda: defaultdict(list))
    amine_infer = defaultdict(lambda: defaultdict(list))

    for r in rows:
        if not r["paper_id"].strip().isdigit():
            continue
        ald = r["aldehyde_smiles"]
        amine = r["amine_smiles"]
        for field in CSV_FIELDS:
            if r.get(field) and r[field].strip():
                if ald:
                    ald_infer[ald][field].append(r[field])
                if amine:
                    amine_infer[amine][field].append(r[field])

    # 取众数
    ald_mode = {}
    for ald, fields in ald_infer.items():
        ald_mode[ald] = {}
        for field, vals in fields.items():
            c = Counter(vals)
            ald_mode[ald][field] = c.most_common(1)[0][0] if c else None

    amine_mode = {}
    for amine, fields in amine_infer.items():
        amine_mode[amine] = {}
        for field, vals in fields.items():
            c = Counter(vals)
            amine_mode[amine][field] = c.most_common(1)[0][0] if c else None

    return ald_mode, amine_mode


def fill_conditions(csv_path: Path, conditions: dict, ald_mode: dict, amine_mode: dict,
                     output_path: Path) -> dict:
    """补全 CSV 条件字段。"""
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    # 确保输出字段包含新增字段
    for nf in ["time", "catalyst_volume", "atmosphere", "additive"]:
        if nf not in fieldnames:
            fieldnames.append(nf)

    stats = {
        "total_rows": len(rows),
        "yaml_matched": 0,
        "inferred": 0,
        "fills": {},
    }
    for csv_field in CSV_FIELDS:
        stats["fills"][csv_field] = {"before": 0, "after": 0, "yaml_filled": 0, "inferred": 0}

    filled_rows = []

    for row in rows:
        paper_id_raw = row.get("paper_id", "").strip()
        group_id_raw = row.get("group_id", "").strip()

        # 统计补全前
        for csv_field in CSV_FIELDS:
            if not _is_empty(row.get(csv_field)):
                stats["fills"][csv_field]["before"] += 1

        # 阶段 1: YAML 精确匹配（仅 int paper_id）
        if paper_id_raw.isdigit():
            try:
                key = (int(paper_id_raw), int(group_id_raw))
                yaml_record = conditions.get(key)
                if yaml_record:
                    stats["yaml_matched"] += 1
                    for csv_field, val in yaml_record.items():
                        if _is_empty(row.get(csv_field)):
                            row[csv_field] = val
                            stats["fills"][csv_field]["yaml_filled"] += 1
            except ValueError:
                pass

        # 阶段 2: 单体推断（aug_/hr_/cr_ 行）
        if not paper_id_raw.isdigit():
            ald = row.get("aldehyde_smiles")
            amine = row.get("amine_smiles")
            inferred_any = False
            for field in CSV_FIELDS:
                if not _is_empty(row.get(field)):
                    continue
                # 优先用醛推断，其次用胺推断
                val = None
                if ald and ald in ald_mode and ald_mode[ald].get(field):
                    val = ald_mode[ald][field]
                elif amine and amine in amine_mode and amine_mode[amine].get(field):
                    val = amine_mode[amine][field]
                if val:
                    row[field] = val
                    stats["fills"][field]["inferred"] += 1
                    inferred_any = True
            if inferred_any:
                stats["inferred"] += 1

        # 统计补全后
        for csv_field in CSV_FIELDS:
            if not _is_empty(row.get(csv_field)):
                stats["fills"][csv_field]["after"] += 1

        filled_rows.append(row)

    # 写入输出 CSV
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(filled_rows)

    return stats


def print_report(stats: dict) -> None:
    print()
    print("=" * 70)
    print("Condition Fill Report")
    print("=" * 70)
    print(f"Total rows: {stats['total_rows']}")
    print(f"YAML matched: {stats['yaml_matched']}")
    print(f"Monomer inferred: {stats['inferred']}")
    print()
    print(f"{'Field':<18} {'Before':>8} {'After':>8} {'+YAML':>8} {'+Infer':>8} {'Gain':>8}")
    print("-" * 70)
    for csv_field, counts in stats["fills"].items():
        before = counts["before"]
        after = counts["after"]
        yaml_f = counts["yaml_filled"]
        inf = counts["inferred"]
        total = stats["total_rows"]
        gain = (after - before) / total * 100 if total else 0
        print(f"{csv_field:<18} {before:>7} {after:>7} {yaml_f:>7} {inf:>7} {gain:>7.1f}%")
    print("=" * 70)


def main() -> None:
    print("=" * 70)
    print("YAML + Monomer Inference -> CSV Condition Fill")
    print("=" * 70)
    print()

    print("Step 1/3: Load YAML conditions...")
    conditions = load_yaml_conditions()
    print(f"  Loaded {len(conditions)} YAML condition records")

    print()
    print("Step 2/3: Build monomer inference library...")
    with open(INPUT_CSV, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    ald_mode, amine_mode = build_monomer_inference(rows)
    print(f"  Ald modes: {len(ald_mode)}, Amine modes: {len(amine_mode)}")

    print()
    print("Step 3/3: Fill conditions...")
    stats = fill_conditions(INPUT_CSV, conditions, ald_mode, amine_mode, OUTPUT_CSV)

    print()
    print_report(stats)

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print(f"JSON report: {OUTPUT_JSON}")
    print(f"Filled CSV: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
