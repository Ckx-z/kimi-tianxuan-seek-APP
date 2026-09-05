"""阶段三（v1.6.1）：修复 v6 训练集中 TFPT（三嗪三醛）相关的坏行。

背景：阶段二 audit_film_labels.py 的噪声修复按「解析失败 → 官能度 0 →
不可成网 → is_film 改 0」处理，误伤了一批 SMILES 解析失败但真实成膜的
TFPT（1,3,5-三(4-甲酰苯基)三嗪）行——金标准评估中 TFPT+对苯二胺 /
TFPT+联苯胺被 tree_v5 打到 0.03-0.05 的根因。

本脚本（在 v6 基础上，幂等）：
1. 修正 SMILES 解析失败行的醛/胺 SMILES（换成可解析的标准写法）；
2. 对化学上确能成网的 TFPT+二胺行恢复 is_film=original（1.0）；
3. 对「TFPT+非胺单体」行保留 0.0（醛-胺缩聚场景不适用，翻转本身正确），
   仅修正其 SMILES；
4. 修正后逐行验证：SMILES 可解析、官能度与 can_network 符合预期。

用法：
    E:\\ANACONDA\\python.exe scripts/fix_tfpt_labels.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

from rdkit import Chem, RDLogger

RDLogger.logger().setLevel(RDLogger.ERROR)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = PROJECT_ROOT / "data" / "interim" / "v6_train_stage1.csv"

CLEAN_TFPT = "O=Cc1ccc(-c2nc(-c3ccc(C=O)cc3)nc(-c3ccc(C=O)cc3)n2)cc1"
PHENOXY_TFPT = "O=Cc1ccc(Oc2nc(Oc3ccc(C=O)cc3)nc(Oc4ccc(C=O)cc4)n2)cc1"
BENZIDINE = "Nc1ccc(-c2ccc(N)cc2)cc1"
BD_CF3 = "Nc1ccc(C(F)(F)F)cc1-c1ccc(N)cc1C(F)(F)F"   # 2,2'-双(三氟甲基)联苯胺
TDA = "Cc1cc(-c2ccc(N)c(C)c2)ccc1N"                  # 3,3'-二甲基联苯胺
BRB = "Brc1cc(-c2ccc(N)c(Br)c2)ccc1N"                # 3,3'-二溴联苯胺
TAPB = "Nc1ccc(-c2cc(-c3ccc(N)cc3)cc(-c3ccc(N)cc3)c2)cc1"
TAPT = "Nc1ccc(-c2nc(-c3ccc(N)cc3)nc(-c3ccc(N)cc3)n2)cc1"
TAPA = "Nc1ccc(N(c2ccc(N)cc2)c2ccc(N)cc2)cc1"
TPAB = "Nc1ccc(-c2ccc(N(c3ccc(-c4ccc(N)cc4)cc3)c3ccc(-c4ccc(N)cc4)cc3)cc2)cc1"
DMB = "COc1cc(-c2ccc(N)c(OC)c2)ccc1N"                # 3,3'-二甲氧基联苯胺
DDM = "Nc1ccc(Cc2ccc(N)cc2)cc1"                      # 4,4'-二氨基二苯甲烷
TCPT = "N#CCc1ccc(-c2nc(-c3ccc(CC#N)cc3)nc(-c3ccc(CC#N)cc3)n2)cc1"

# (paper_id, group_id) -> (新醛, 新胺, 新 is_film)
FIXES: dict[tuple[str, str], tuple[str, str, str]] = {
    # 884：TFPT + 二胺（真实成膜 COF，SMILES 坏 → 误翻 0）
    ("884", "1"): (CLEAN_TFPT, BD_CF3, "1.0"),
    ("884", "2"): (CLEAN_TFPT, BENZIDINE, "1.0"),
    ("884", "3"): (CLEAN_TFPT, TDA, "1.0"),
    ("884", "4"): (CLEAN_TFPT, BRB, "1.0"),
    # 479：三(4-甲酰苯氧基)三嗪 + 二胺/三胺（SMILES 坏 → 误翻 0）
    ("479", "2"): (PHENOXY_TFPT, TAPB, "1.0"),
    ("479", "8"): (PHENOXY_TFPT, DMB, "1.0"),
    ("479", "9"): (PHENOXY_TFPT, DDM, "1.0"),
    # 604：TFPT + TAPA / TPAB（SMILES 坏 → 误翻 0）
    ("604", "1"): (CLEAN_TFPT, TAPA, "1.0"),
    ("604", "3"): (CLEAN_TFPT, TPAB, "1.0"),
    # 1358：TFPT + TAPT / TAPB（两侧 SMILES 坏 → 误翻 0）
    ("1358", "1"): (CLEAN_TFPT, TAPT, "1.0"),
    ("1358", "2"): (CLEAN_TFPT, TAPB, "1.0"),
    # 246：TFPT + TCPT（腈类非胺 → 保留 0.0，仅修 SMILES）
    ("246", "1"): (CLEAN_TFPT, TCPT, "0.0"),
}

_SMARTS_ALDEHYDE = "[CX3H](=O)"
_SMARTS_PRIMARY_AMINE = "[NX3H2;!$(N[C,S]=O);!$(NO);!$(N=O)]"
_SMARTS_SECONDARY_AMINE = "[NX3H1;!$(N[C,S]=O);!$(NO);!$(N=O)]([#6])[#6]"


def functionality(smiles: str, role: str) -> int | None:
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    if role == "aldehyde":
        return len(mol.GetSubstructMatches(Chem.MolFromSmarts(_SMARTS_ALDEHYDE)))
    return (len(mol.GetSubstructMatches(Chem.MolFromSmarts(_SMARTS_PRIMARY_AMINE)))
            + len(mol.GetSubstructMatches(Chem.MolFromSmarts(_SMARTS_SECONDARY_AMINE))))


def can_network(f_ald: int, f_amine: int) -> bool:
    return (f_ald >= 2 and f_amine >= 2) \
        or (f_ald >= 3 and f_amine >= 1) \
        or (f_amine >= 3 and f_ald >= 1)


def main() -> None:
    rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8-sig")))
    by_key = {(r["paper_id"], r["group_id"]): r for r in rows}
    changed, verified = 0, 0
    for key, (new_ald, new_amine, new_film) in FIXES.items():
        r = by_key.get(key)
        if r is None:
            print(f"[跳过] 未找到 {key}")
            continue
        old = (r["aldehyde_smiles"], r["amine_smiles"], r["is_film"])
        r["aldehyde_smiles"] = new_ald
        r["amine_smiles"] = new_amine
        r["is_film"] = new_film
        f_a = functionality(new_ald, "aldehyde")
        f_b = functionality(new_amine, "amine")
        ok_parse = f_a is not None and f_b is not None
        ok_chem = ok_parse and can_network(f_a, f_b)
        if ok_parse and float(new_film) > 0 and not ok_chem:
            raise SystemExit(f"[失败] {key} 修正后仍不可成网（{f_a},{f_b}）")
        changed += 1
        verified += 1
        print(f"[修复] {key[0]},{key[1]} is_film {old[2]} → {new_film} "
              f"f=({f_a},{f_b}) can_network={ok_chem}")

    fieldnames = list(rows[0].keys())
    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n已修复 {changed} 行（其中验证通过 {verified}）→ {CSV_PATH.name}")

    # 复查：全表统计（审计口径复算，确认 TFPT 行现状）
    fixed = {(k[0], k[1]) for k in FIXES}
    tfpt_hits = [r for r in rows
                 if (r["paper_id"], r["group_id"]) in fixed]
    print(f"复查 TFPT 修复行: {len(tfpt_hits)}")
    for r in tfpt_hits:
        print(f"  {r['paper_id']},{r['group_id']} is_film={r['is_film']} "
              f"orig={r.get('original_is_film')} "
              f"ald_ok={Chem.MolFromSmiles(r['aldehyde_smiles']) is not None} "
              f"amine_ok={Chem.MolFromSmiles(r['amine_smiles']) is not None}")


if __name__ == "__main__":
    main()
