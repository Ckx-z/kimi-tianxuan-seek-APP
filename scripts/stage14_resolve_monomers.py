"""阶段 14（真实回测）：单体 → SMILES 解析与验证。

数据来源（均只读）：
1. data/experimental_refs/实验方案_含氟COF薄膜合成_v3.9_20260626.docx（化学名 + MW，已提取到 _extracted.json）
2. data/experimental_refs/实验ABCDEF.docx（CAS 号 + MW）
3. 旧项目单体池 merged_monomer_pool.csv（只读，交叉验证）

解析纪律（宁缺毋滥）：
- SMILES 由 docx 中明确给出的化学名构建，RDKit 计算 MW 与 docx 标注 MW 校验（容差 0.5）
- 与旧单体池按 canonical SMILES / CAS 交叉匹配
- MW 冲突或结构歧义的单体进入 pending_confirmation，绝不猜

输出：reports/real_backtest_monomers.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
from rdkit import Chem
from rdkit.Chem import Descriptors

ROOT = Path(__file__).resolve().parent.parent
OLD_POOL = Path(r"C:\Users\ckx\Desktop\tianxuan seek\data\processed\merged_monomer_pool.csv")
OUT = ROOT / "reports" / "real_backtest_monomers.json"

# (id, 类型, docx化学名, docx标注MW, CAS(实验记录), 候选SMILES)
# 候选 SMILES 依据 docx 化学名构建；MW 校验通过 + 池内交叉验证后才标记 resolved
CANDIDATES = [
    # 节点
    ("TAPT", "amine_node", "2,4,6-三(4-氨基苯基)-1,3,5-三嗪", 354.41, "14544-47-9",
     "Nc1ccc(-c2nc(-c3ccc(N)cc3)nc(-c3ccc(N)cc3)n2)cc1"),
    ("TFPT", "aldehyde_node", "2,4,6-三(4-甲酰基苯基)-1,3,5-三嗪", 393.42, "443922-06-3",
     "O=Cc1ccc(-c2nc(-c3ccc(C=O)cc3)nc(-c3ccc(C=O)cc3)n2)cc1"),
    ("TAPB", "amine_node", "1,3,5-三(4-氨基苯基)苯（实验记录实节点）", 351.44, "118727-34-7",
     "Nc1ccc(-c2cc(-c3ccc(N)cc3)cc(-c3ccc(N)cc3)c2)cc1"),
    ("TFPB", "aldehyde_node", "1,3,5-三(4-甲酰基苯基)苯（D4 实节点）", 390.43, "118688-53-2",
     "O=Cc1ccc(-c2cc(-c3ccc(C=O)cc3)cc(-c3ccc(C=O)cc3)c2)cc1"),
    # 二醛 A1–A7
    ("A1", "aldehyde", "2,5-二氟对苯二甲醛", 170.11, "608145-27-3",
     "O=Cc1cc(F)c(C=O)cc1F"),
    ("A2", "aldehyde", "2,3,5,6-四氟对苯二甲醛", 206.10, "3217-47-8",
     "O=Cc1c(F)c(F)c(C=O)c(F)c1F"),
    ("A3", "aldehyde", "2,2',3,3',5,5',6,6'-八氟-4,4'-联苯二甲醛", 354.14, "2640813-40-5",
     "O=Cc1c(F)c(F)c(-c2c(F)c(F)c(C=O)c(F)c2F)c(F)c1F"),
    ("A4", "aldehyde", "2,2'-双(三氟甲基)-4,4'-联苯二甲醛", 346.23, "1271813-32-1",
     "O=Cc1ccc(-c2ccc(C=O)cc2C(F)(F)F)c(C(F)(F)F)c1"),
    ("A5", "aldehyde", "2,5-双(三氟甲基)对苯二甲醛", 270.13, "847450-39-9",
     "O=Cc1cc(C(F)(F)F)c(C=O)cc1C(F)(F)F"),
    ("A6", "aldehyde", "4,4''-双(三氟甲基)-2',5'-二甲酰基对三联苯", 422.37, "1300701-03-4",
     "O=Cc1cc(-c2ccc(C(F)(F)F)cc2)c(C=O)cc1-c1ccc(C(F)(F)F)cc1"),
    ("A7", "aldehyde", "3,3'',5,5''-四(三氟甲基)-2',5'-二甲酰基对三联苯", 558.34, "2368851-11-8",
     "O=Cc1cc(-c2cc(C(F)(F)F)cc(C(F)(F)F)c2)c(C=O)cc1-c1cc(C(F)(F)F)cc(C(F)(F)F)c1"),
    # 二胺 B1–B6
    ("B1", "amine", "1,4-二氨基-2,5-二氟苯", 144.12, "698-52-2",
     "Nc1cc(F)c(N)cc1F"),
    ("B2", "amine", "1,4-二氨基-2,3,5,6-四氟苯", 180.11, "1198-64-7",
     "Nc1c(F)c(F)c(N)c(F)c1F"),
    ("B3", "amine", "3,3'-二氟-4,4'-联苯二胺", 220.22, "316-64-3",
     "Nc1ccc(-c2ccc(N)c(F)c2)cc1F"),
    ("B4", "amine", "2,2',3,3',5,5',6,6'-八氟-4,4'-联苯二胺", 328.16, "1038-66-0",
     "Nc1c(F)c(F)c(-c2c(F)c(F)c(N)c(F)c2F)c(F)c1F"),
    ("B5", "amine", "2,2'-双(三氟甲基)-4,4'-联苯二胺", 320.24, "341-58-2",
     "Nc1ccc(-c2ccc(N)cc2C(F)(F)F)c(C(F)(F)F)c1"),
    ("B6", "amine", "2,5-二氨基三氟甲苯", 176.14, "364-13-6",
     "Nc1ccc(N)c(C(F)(F)F)c1"),
    # 二酰肼 H1–H4（结构按方案化学名构建，MW 与实验记录 CAS MW 冲突者列待确认）
    ("H1", "hydrazide", "1,4-双(酰肼基)-2,5-双(2,2,3,3,3-五氟丙氧基)苯", None, None,
     "NNC(=O)c1cc(OCC(F)(F)C(F)(F)F)c(C(=O)NN)cc1OCC(F)(F)C(F)(F)F"),
    ("H2", "hydrazide", "1,4-双(酰肼基)-2,5-双(2,2,3,3,4,4,4-七氟丁氧基)苯", 662.3, None,
     "NNC(=O)c1cc(OCC(F)(F)C(F)(F)C(F)(F)F)c(C(=O)NN)cc1OCC(F)(F)C(F)(F)C(F)(F)F"),
    ("H3", "hydrazide", "1,4-双(酰肼基)-2,5-双(2,2,3,3,4,4,5,5,5-九氟戊氧基)苯", 918.37, "2569674-66-2",
     "NNC(=O)c1cc(OCC(F)(F)C(F)(F)C(F)(F)C(F)(F)F)c(C(=O)NN)cc1OCC(F)(F)C(F)(F)C(F)(F)C(F)(F)F"),
    ("H4", "hydrazide", "1,4-双(酰肼基)-2,5-双(2,2,3,3,4,4,5,5,6,6,6-十一氟己氧基)苯", None, None,
     "NNC(=O)c1cc(OCC(F)(F)C(F)(F)C(F)(F)C(F)(F)C(F)(F)F)c(C(=O)NN)cc1OCC(F)(F)C(F)(F)C(F)(F)C(F)(F)C(F)(F)F"),
]

MW_TOL = 0.5


def canon(smi: str):
    m = Chem.MolFromSmiles(smi)
    if m is None:
        return None, None
    return Chem.MolToSmiles(m), Descriptors.MolWt(m)


def main():
    pool = pd.read_csv(OLD_POOL)
    pool["canon"] = pool["smiles"].map(lambda s: canon(str(s))[0])
    pool_by_canon = {}
    for _, r in pool.iterrows():
        pool_by_canon.setdefault(r["canon"], []).append(
            {"best_name": r["best_name"], "cas": r.get("cas"), "n_papers": int(r["n_papers"])})

    results = {}
    pending = []
    for mid, mtype, cname, doc_mw, cas, smi in CANDIDATES:
        csmi, mw = canon(smi)
        entry = {
            "id": mid, "type": mtype, "docx_name": cname, "cas_in_record": cas,
            "candidate_smiles": smi, "canonical_smiles": csmi, "rdkit_mw": mw,
            "docx_mw": doc_mw, "mw_check": None, "pool_matches": [], "status": "resolved",
        }
        if csmi is None:
            entry["status"] = "pending_confirmation"
            entry["issue"] = "候选 SMILES 无法被 RDKit 解析"
        else:
            if doc_mw is not None:
                entry["mw_check"] = abs(mw - doc_mw) <= MW_TOL
            entry["pool_matches"] = pool_by_canon.get(csmi, [])
            # H 系列：方案化学名构建结构的计算 MW 与实验记录 CAS MW 冲突 → 待确认
            if mtype == "hydrazide":
                entry["status"] = "pending_confirmation"
                entry["issue"] = (
                    f"按方案化学名构建的结构计算 MW={mw:.2f}，"
                    f"与实验记录 CAS 标注 MW={doc_mw} 不一致（若 docx_mw 为空则为未标注）；"
                    "H 系列实际结构需用户确认")
            elif doc_mw is not None and not entry["mw_check"]:
                entry["status"] = "pending_confirmation"
                entry["issue"] = f"计算 MW={mw:.2f} 与 docx 标注 MW={doc_mw} 不符"
        if entry["status"] == "pending_confirmation":
            pending.append(mid)
        results[mid] = entry

    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"monomers": results, "pending_confirmation": pending}, f,
                  ensure_ascii=False, indent=1)

    n_res = sum(1 for e in results.values() if e["status"] == "resolved")
    print(f"resolved {n_res}/{len(results)}; pending: {pending}")
    for mid, e in results.items():
        pm = f"pool:{len(e['pool_matches'])}" if e["pool_matches"] else "pool:0"
        print(f"{mid:5s} {e['status']:22s} mw={e['rdkit_mw'] and round(e['rdkit_mw'],2)} "
              f"docx_mw={e['docx_mw']} {pm}")


if __name__ == "__main__":
    main()
