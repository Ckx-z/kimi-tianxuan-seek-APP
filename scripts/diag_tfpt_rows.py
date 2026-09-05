"""诊断：TFPT（三嗪三醛）相关行在 v6 训练集中的标签与 SMILES 解析情况。

用于定位阶段三金标准 A 类误判根因（TFPT+对苯二胺 tree=0.048）。
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

from rdkit import Chem, RDLogger

RDLogger.logger().setLevel(RDLogger.ERROR)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = PROJECT_ROOT / "data" / "interim" / "v6_train_stage1.csv"

_SMARTS_ALDEHYDE = "[CX3H](=O)"

CLEAN_TFPT = "O=Cc1ccc(-c2nc(-c3ccc(C=O)cc3)nc(-c3ccc(C=O)cc3)n2)cc1"


def n_aldehyde(smiles: str) -> int | None:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    return len(mol.GetSubstructMatches(Chem.MolFromSmarts(_SMARTS_ALDEHYDE)))


def canonical(smiles: str) -> str | None:
    mol = Chem.MolFromSmiles(smiles)
    return Chem.MolToSmiles(mol) if mol is not None else None


def main() -> None:
    print("== 单条 SMILES 解析 ==")
    bad = "O=C(c1cc(-c2nc(-c3ccc(C=O)c3)nc(-c3ccc(C=O)c3)n2)ncn1)c1ccc(C=O)c1"
    print(f"884 行坏 SMILES 解析: {Chem.MolFromSmiles(bad) is not None}, "
          f"醛基数={n_aldehyde(bad)}")
    print(f"清洁 TFPT SMILES 解析: {Chem.MolFromSmiles(CLEAN_TFPT) is not None}, "
          f"醛基数={n_aldehyde(CLEAN_TFPT)}")

    rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8-sig")))
    print(f"\n== v6 训练集总行数: {len(rows)} ==")

    # 找出含三嗪三醛结构（清洁 TFPT）或名称含 TFPT 的行
    clean = Chem.MolFromSmiles(CLEAN_TFPT)
    tfpt_rows = []
    for r in rows:
        mol = Chem.MolFromSmiles(r["aldehyde_smiles"])
        name = (r.get("aldehyde_name") or "")
        name_hit = "TFPT" in name
        if mol is None:
            if name_hit:
                tfpt_rows.append((r, "PARSE_FAIL", None))
            continue
        if mol.HasSubstructMatch(clean) or name_hit:
            na = n_aldehyde(r["aldehyde_smiles"])
            if na is None or na >= 2:
                tfpt_rows.append((r, "ok", na))
    print(f"TFPT 相关行: {len(tfpt_rows)}")
    for r, flag, na in tfpt_rows:
        ald = r["aldehyde_smiles"][:62]
        amine = r["amine_smiles"][:34]
        print(f"  {r['paper_id']:>5},{r['group_id']:>3} is_film={r['is_film']} "
              f"orig={r.get('original_is_film')} f_ald={na} [{flag}]")
        print(f"      ald={ald}")
        print(f"      amine={amine}")

    # 统计：清洁 TFPT 单体（精确匹配）在训练集中出现的行
    print("\n== 清洁 TFPT 单体精确出现 ==")
    for r in rows:
        if canonical(r["aldehyde_smiles"]) == canonical(CLEAN_TFPT):
            print(f"  {r['paper_id']},{r['group_id']} is_film={r['is_film']} "
                  f"orig={r.get('original_is_film')} + {r['amine_smiles'][:40]}")


if __name__ == "__main__":
    main()
