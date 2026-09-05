"""成膜标签审计（v1.6.1 阶段二）：找出「化学上不可能成膜却标注成膜」的噪声样本。

判定：成网必要条件 = 醛位点数≥2 且 胺位点数≥2，或任一侧≥3 且另一侧≥1
（与 src/predictor/ood.py::check_networkability 同口径）。不满足且
is_film>0 的样本列入噪声清单；不满足且 is_film==0 为合法负样本。

用法：
    python scripts/audit_film_labels.py                     # 只出报告
    python scripts/audit_film_labels.py --fix --out data/interim/v6_train_stage1.csv
                                                            # 噪声标签改 0 并写 v6 CSV

注意（v1.6.1 阶段三）：--fix 会把「SMILES 解析失败」的行按官能度 0 处理
并翻成 0——这会误伤 TFPT（三嗪三醛）等真实成膜行。--fix 之后必须再跑
    python scripts/fix_tfpt_labels.py
恢复 TFPT 行标签并修正其 SMILES（幂等，金标准评估依赖该顺序）。
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from rdkit import Chem, RDLogger
RDLogger.logger().setLevel(RDLogger.ERROR)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

CSV_PATH = PROJECT_ROOT / "data" / "interim" / "v5_train_stage1_cond_filled.csv"

_SMARTS_ALDEHYDE = "[CX3H](=O)"
_SMARTS_PRIMARY_AMINE = "[NX3H2;!$(N[C,S]=O);!$(NO);!$(N=O)]"
_SMARTS_SECONDARY_AMINE = "[NX3H1;!$(N[C,S]=O);!$(NO);!$(N=O)]([#6])[#6]"
_PATTERNS = {
    "aldehyde": Chem.MolFromSmarts(_SMARTS_ALDEHYDE),
    "primary_amine": Chem.MolFromSmarts(_SMARTS_PRIMARY_AMINE),
    "secondary_amine": Chem.MolFromSmarts(_SMARTS_SECONDARY_AMINE),
}


def functionality(smiles: str, role: str) -> int:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return 0
    if role == "aldehyde":
        return len(mol.GetSubstructMatches(_PATTERNS["aldehyde"]))
    return (len(mol.GetSubstructMatches(_PATTERNS["primary_amine"]))
            + len(mol.GetSubstructMatches(_PATTERNS["secondary_amine"])))


def can_network(f_ald: int, f_amine: int) -> bool:
    return (f_ald >= 2 and f_amine >= 2) \
        or (f_ald >= 3 and f_amine >= 1) \
        or (f_amine >= 3 and f_ald >= 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true",
                    help="把噪声标签（不可成网却 is_film>0）改写为 0")
    ap.add_argument("--out", default=None, help="修复后的 CSV 输出路径")
    args = ap.parse_args()

    rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8-sig")))
    noise: list[dict] = []
    legit_neg: list[dict] = []
    stats = {"noise_rows": 0, "noise_film1": 0, "legit_neg": 0}
    for r in rows:
        f_a = functionality(r["aldehyde_smiles"], "aldehyde")
        f_b = functionality(r["amine_smiles"], "amine")
        ok = can_network(f_a, f_b)
        film = float(r.get("is_film") or 0)
        if not ok:
            rec = {"aldehyde_smiles": r["aldehyde_smiles"],
                   "amine_smiles": r["amine_smiles"],
                   "f_ald": f_a, "f_amine": f_b, "is_film": film,
                   "paper_id": r.get("paper_id") or "",
                   "group_id": r.get("group_id") or ""}
            if film > 0:
                noise.append(rec)
                stats["noise_rows"] += 1
                if film >= 1:
                    stats["noise_film1"] += 1
            else:
                legit_neg.append(rec)
                stats["legit_neg"] += 1

    print("== 审计结果 ==")
    print(f"总行数: {len(rows)}")
    print(f"不可成网且标注成膜（噪声）: {stats['noise_rows']} 行"
          f"（其中 is_film=1 的 {stats['noise_film1']} 行）")
    print(f"不可成网且标注未成膜（合法负样本）: {stats['legit_neg']} 行")
    print("\n噪声样本按单体对（前 15）：")
    seen: dict[tuple[str, str], int] = {}
    for n in noise:
        k = (n["aldehyde_smiles"], n["amine_smiles"])
        seen[k] = seen.get(k, 0) + 1
    for (a, b), c in sorted(seen.items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {c:>3} × {a} + {b}")

    if args.fix:
        out = Path(args.out) if args.out else CSV_PATH.with_name("v6_train_stage1.csv")
        fixed = 0
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            for r in rows:
                f_a = functionality(r["aldehyde_smiles"], "aldehyde")
                f_b = functionality(r["amine_smiles"], "amine")
                if not can_network(f_a, f_b) and float(r.get("is_film") or 0) > 0:
                    r = dict(r)
                    r["is_film"] = "0.0"
                    fixed += 1
                writer.writerow(r)
        print(f"\n已修复 {fixed} 行噪声标签 → {out}")


if __name__ == "__main__":
    main()
