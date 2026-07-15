"""数据审计工具：评估 v5_train_stage1.csv 的数据质量。

输出：
- data/interim/audit_report.json
- data/interim/audit_report.md

审计内容：覆盖率、缺失率、标签冲突、异常值等。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

NEW_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_RAW_DIR = NEW_PROJECT_ROOT / "data" / "raw"
DATA_INTERIM_DIR = NEW_PROJECT_ROOT / "data" / "interim"

REQUIRED_COLUMNS = [
    "paper_id",
    "group_id",
    "source_db",
    "aldehyde_smiles",
    "amine_smiles",
    "aldehyde_name",
    "amine_name",
    "stoichiometry",
    "solvent",
    "temperature",
    "catalyst",
    "synthesis_route",
    "interface_type",
    "is_film",
    "film_quality",
    "original_is_film",
]

CONDITION_COLUMNS = ["stoichiometry", "solvent", "temperature", "catalyst", "synthesis_route", "interface_type"]


def parse_temperature_to_celsius(temp: str) -> float | None:
    """粗略解析温度字符串，返回摄氏度数值。"""
    if pd.isna(temp):
        return None
    s = str(temp).strip().lower()
    if not s or s in {"nan", "none", "null", "", "ambient"}:
        return None
    # 处理 "室温 (25°C)"、"120 °C" 等
    import re

    match = re.search(r"(-?\d+(?:\.\d+)?)", s)
    if not match:
        return None
    val = float(match.group(1))
    if "k" in s:
        val = val - 273.15
    return val


def audit_data(df: pd.DataFrame) -> dict:
    """对 DataFrame 进行全面审计。"""
    report: dict = {}

    # 1. 基本信息
    report["basic"] = {
        "n_rows": int(len(df)),
        "n_columns": int(len(df.columns)),
        "columns": list(df.columns),
        "missing_required_columns": [c for c in REQUIRED_COLUMNS if c not in df.columns],
    }

    # 2. 缺失率
    missing_rates = (df.isna().sum() / len(df)).to_dict()
    report["missing_rates"] = {k: round(float(v), 4) for k, v in missing_rates.items()}

    # 3. SMILES 缺失
    smiles_missing = df["aldehyde_smiles"].isna() | df["amine_smiles"].isna()
    report["smiles"] = {
        "n_missing_aldehyde": int(df["aldehyde_smiles"].isna().sum()),
        "n_missing_amine": int(df["amine_smiles"].isna().sum()),
        "n_missing_either": int(smiles_missing.sum()),
        "missing_rate": round(float(smiles_missing.mean()), 4),
    }

    # 4. 反应条件缺失率
    report["condition_missing_rates"] = {
        col: round(float(df[col].isna().mean()), 4) for col in CONDITION_COLUMNS if col in df.columns
    }

    # 5. 标签分布
    if "is_film" in df.columns:
        label_counts = df["is_film"].value_counts().sort_index().to_dict()
        report["label_distribution"] = {str(k): int(v) for k, v in label_counts.items()}
    else:
        report["label_distribution"] = {}

    # 6. 标签冲突：同醛+胺+条件在不同文献中标签不同
    # 用简单分组：同 aldehyde_smiles + amine_smiles + solvent + temperature
    group_cols = [c for c in ["aldehyde_smiles", "amine_smiles", "solvent", "temperature"] if c in df.columns]
    if group_cols and "is_film" in df.columns:
        grouped = df.groupby(group_cols, dropna=False)["is_film"].nunique()
        conflicts = grouped[grouped > 1]
        report["label_conflicts"] = {
            "n_conflict_groups": int(len(conflicts)),
            "n_conflict_rows": int(df.set_index(group_cols).index.isin(conflicts.index).sum()),
        }
    else:
        report["label_conflicts"] = {"n_conflict_groups": 0, "n_conflict_rows": 0}

    # 7. original_is_film 与 is_film 不一致
    if "original_is_film" in df.columns and "is_film" in df.columns:
        # 二值化对比：is_film >= 0.5 视为正
        df_temp = df.copy()
        df_temp["pred_bin"] = (df_temp["is_film"] >= 0.5).astype(int)
        mismatch = (df_temp["original_is_film"] != df_temp["pred_bin"]).sum()
        report["label_mismatch"] = {
            "n_mismatch": int(mismatch),
            "mismatch_rate": round(float(mismatch / len(df)), 4),
        }
    else:
        report["label_mismatch"] = {"n_mismatch": 0, "mismatch_rate": 0.0}

    # 8. 重复样本（按醛/胺/条件）
    dedup_cols = [c for c in ["aldehyde_smiles", "amine_smiles", "solvent", "temperature"] if c in df.columns]
    if dedup_cols:
        n_unique = df.drop_duplicates(subset=dedup_cols).shape[0]
        report["duplicates"] = {
            "n_total": int(len(df)),
            "n_unique": int(n_unique),
            "n_duplicates": int(len(df) - n_unique),
        }
    else:
        report["duplicates"] = {"n_total": int(len(df)), "n_unique": int(len(df)), "n_duplicates": 0}

    # 9. 异常值
    anomalies = {}
    # 9.1 is_film=1 但 SMILES 缺失
    if "is_film" in df.columns:
        anomalies["film_but_missing_smiles"] = int(
            ((df["is_film"] >= 0.5) & smiles_missing).sum()
        )
    # 9.2 温度解析失败的行数
    if "temperature" in df.columns:
        parsed = df["temperature"].apply(parse_temperature_to_celsius)
        anomalies["temperature_parse_fail"] = int(parsed.isna().sum())
    # 9.3 异常温度（>300°C 或 <-100°C）
        parsed_valid = parsed.dropna()
        anomalies["temperature_extreme"] = int(
            ((parsed_valid > 300) | (parsed_valid < -100)).sum()
        )
    report["anomalies"] = anomalies

    return report


def format_report_md(report: dict) -> str:
    """将审计报告格式化为 Markdown。"""
    lines = ["# 数据审计报告", ""]

    lines.append("## 1. 基本信息")
    basic = report["basic"]
    lines.append(f"- 样本数：{basic['n_rows']}")
    lines.append(f"- 字段数：{basic['n_columns']}")
    if basic["missing_required_columns"]:
        lines.append(f"- 缺失必需字段：{basic['missing_required_columns']}")
    else:
        lines.append("- 所有必需字段均存在")
    lines.append("")

    lines.append("## 2. 缺失率")
    for col, rate in report["missing_rates"].items():
        lines.append(f"- {col}: {rate:.2%}")
    lines.append("")

    lines.append("## 3. SMILES 缺失")
    smiles = report["smiles"]
    lines.append(f"- 醛缺失：{smiles['n_missing_aldehyde']}")
    lines.append(f"- 胺缺失：{smiles['n_missing_amine']}")
    lines.append(f"- 任一缺失：{smiles['n_missing_either']} ({smiles['missing_rate']:.2%})")
    lines.append("")

    lines.append("## 4. 反应条件缺失率")
    for col, rate in report["condition_missing_rates"].items():
        lines.append(f"- {col}: {rate:.2%}")
    lines.append("")

    lines.append("## 5. 标签分布")
    for label, count in report["label_distribution"].items():
        lines.append(f"- {label}: {count}")
    lines.append("")

    lines.append("## 6. 标签冲突（同组合+条件在不同文献标签不同）")
    conflicts = report["label_conflicts"]
    lines.append(f"- 冲突组数：{conflicts['n_conflict_groups']}")
    lines.append(f"- 冲突行数：{conflicts['n_conflict_rows']}")
    lines.append("")

    lines.append("## 7. 标签不一致（original_is_film vs is_film）")
    mismatch = report["label_mismatch"]
    lines.append(f"- 不一致行数：{mismatch['n_mismatch']} ({mismatch['mismatch_rate']:.2%})")
    lines.append("")

    lines.append("## 8. 重复样本")
    dup = report["duplicates"]
    lines.append(f"- 总数：{dup['n_total']}")
    lines.append(f"- 去重后：{dup['n_unique']}")
    lines.append(f"- 重复数：{dup['n_duplicates']}")
    lines.append("")

    lines.append("## 9. 异常值")
    for key, val in report["anomalies"].items():
        lines.append(f"- {key}: {val}")
    lines.append("")

    lines.append("## 10. 建议")
    if report["smiles"]["missing_rate"] > 0.2:
        lines.append("- SMILES 缺失率较高，阶段 2 需要重点补全名称→SMILES 映射。")
    if report["label_conflicts"]["n_conflict_groups"] > 0:
        lines.append("- 存在标签冲突，需建立置信度体系或人工复核。")
    if any(rate > 0.3 for rate in report["condition_missing_rates"].values()):
        lines.append("- 部分反应条件缺失率较高，需评估是否仍能作为有效特征。")
    lines.append("")

    return "\n".join(lines)


def main():
    """命令行入口。"""
    DATA_INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    input_path = DATA_RAW_DIR / "v5_train_stage1.csv"

    if not input_path.exists():
        raise FileNotFoundError(
            f"找不到 {input_path}。请先运行 python src/data/import_data.py"
        )

    print(f"读取数据：{input_path}")
    df = pd.read_csv(input_path)
    print(f"样本数：{len(df)}，字段数：{len(df.columns)}")

    report = audit_data(df)

    # 保存 JSON
    json_path = DATA_INTERIM_DIR / "audit_report.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"JSON 报告已保存：{json_path}")

    # 保存 Markdown
    md_path = DATA_INTERIM_DIR / "audit_report.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(format_report_md(report))
    print(f"Markdown 报告已保存：{md_path}")


if __name__ == "__main__":
    main()
