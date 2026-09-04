"""规则硬负样本生成器（v1.6.1 阶段二）：为成膜打分补充「化学上不可成网」的负样本。

策略：从训练池里挑官能度不足的单体，交叉组合成不可成网的配对：
- 单官能醛 × 全库胺（含双官能胺）→ (1, b) 不可成网
- 全库醛 × 单官能胺 → (a, 1) 不可成网
- 单官能醛 × 单官能胺 → (1, 1)（包含在上面两类中，去重即可）
全部 is_film=0、source_db=hard_negative，追加到（已修复标签的）v6 CSV。

用法：
    python scripts/make_hard_negatives.py \
        --in data/interim/v6_train_stage1.csv \
        --out data/interim/v6_train_stage1.csv --append
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

DEFAULT_IN = PROJECT_ROOT / "data" / "interim" / "v6_train_stage1.csv"

_SMARTS_ALDEHYDE = "[CX3H](=O)"
_SMARTS_PRIMARY_AMINE = "[NX3H2;!$(N[C,S]=O);!$(NO);!$(N=O)]"
_SMARTS_SECONDARY_AMINE = "[NX3H1;!$(N[C,S]=O);!$(NO);!$(N=O)]([#6])[#6]"
_PATTERNS = {
    "aldehyde": Chem.MolFromSmarts(_SMARTS_ALDEHYDE),
    "primary_amine": Chem.MolFromSmarts(_SMARTS_PRIMARY_AMINE),
    "secondary_amine": Chem.MolFromSmarts(_SMARTS_SECONDARY_AMINE),
}
_MAX_PER_SIDE = 40   # 每侧最多取多少个单体（控制生成规模）
_CANON = {}


def canon(smiles: str) -> str | None:
    if smiles in _CANON:
        return _CANON[smiles]
    mol = Chem.MolFromSmiles(smiles)
    out = Chem.MolToSmiles(mol) if mol is not None else None
    _CANON[smiles] = out
    return out


def functionality(smiles: str, role: str) -> int:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return 0
    if role == "aldehyde":
        return len(mol.GetSubstructMatches(_PATTERNS["aldehyde"]))
    return (len(mol.GetSubstructMatches(_PATTERNS["primary_amine"]))
            + len(mol.GetSubstructMatches(_PATTERNS["secondary_amine"])))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default=str(DEFAULT_IN))
    ap.add_argument("--out", default=str(DEFAULT_IN))
    ap.add_argument("--append", action="store_true",
                    help="追加到输出 CSV（默认覆盖）")
    ap.add_argument("--max-generated", type=int, default=600,
                    help="硬负样本生成上限（默认 600，防止淹没正样本）")
    args = ap.parse_args()

    src = Path(args.in_path)
    rows = list(csv.DictReader(open(src, encoding="utf-8-sig")))
    fieldnames = list(rows[0].keys())

    # 收集训练池单体（去重），按官能度分桶
    alds: dict[str, int] = {}
    amines: dict[str, int] = {}
    for r in rows:
        a, b = r["aldehyde_smiles"], r["amine_smiles"]
        if a not in alds:
            alds[a] = functionality(a, "aldehyde")
        if b not in amines:
            amines[b] = functionality(b, "amine")
    mono_alds = [s for s, f in alds.items() if f == 1][:_MAX_PER_SIDE]
    multi_alds = [s for s, f in alds.items() if f >= 2][:_MAX_PER_SIDE]
    mono_amines = [s for s, f in amines.items() if f == 1][:_MAX_PER_SIDE]
    multi_amines = [s for s, f in amines.items() if f >= 2][:_MAX_PER_SIDE]
    print(f"池内单体：醛 {len(alds)}（单官能 {len(mono_alds)}）/ 胺 "
          f"{len(amines)}（单官能 {len(mono_amines)}）")

    existing = {(r["aldehyde_smiles"], r["amine_smiles"]) for r in rows}
    pairs: list[tuple[str, str]] = []
    for a in mono_alds:
        for b in list(mono_amines) + list(multi_amines):
            pairs.append((a, b))          # (1,1) 与 (1,≥2) 均不可成网
    for b in mono_amines:
        for a in multi_alds:
            pairs.append((a, b))          # (≥2,1) 不可成网

    added = 0
    out_rows = list(rows) if args.append else []
    seen = set(existing)
    # 确定性洗牌后截断到上限，保证三类 (1,1)/(1,≥2)/(≥2,1) 均衡取样
    import random
    rng = random.Random(42)
    rng.shuffle(pairs)
    for a, b in pairs:
        if added >= args.max_generated:
            break
        key = (a, b)
        if key in seen:
            continue
        seen.add(key)
        row = {k: "" for k in fieldnames}
        row.update({
            "aldehyde_smiles": a, "amine_smiles": b,
            "is_film": "0.0", "source_db": "hard_negative",
            "paper_id": "hn", "group_id": "hn",
            "aldehyde_name": "", "amine_name": "",
            "film_quality": "none",
        })
        out_rows.append(row)
        added += 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in out_rows:
            writer.writerow(r)
    print(f"生成硬负样本 {added} 条 → {out}（总行数 {len(out_rows)}）")


if __name__ == "__main__":
    main()
