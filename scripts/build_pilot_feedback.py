"""GNN 修正 pilot 数据提取（v1.8.0）：从实验记录提取 TFPT/TFPB × B5/B6 成膜反馈。

半自动：扫描 data/experimental_refs/_extracted.json 段落，按单体映射表
（名称/CAS → canonical SMILES）+ 成膜现象关键词生成候选反馈行，输出
data/feedback/pilot_candidates.csv 供用户逐条复核后再入反馈库。

用法：
    E:\\ANACONDA\\python.exe scripts/build_pilot_feedback.py
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

from rdkit import Chem, RDLogger

RDLogger.logger().setLevel(RDLogger.ERROR)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

EXTRACTED = PROJECT_ROOT / "data" / "experimental_refs" / "_extracted.json"
OUT_CSV = PROJECT_ROOT / "data" / "feedback" / "pilot_candidates.csv"

# 单体映射：名称/CAS 关键词 → canonical SMILES（写入前经 RDKit 校验）
MONOMERS: dict[str, tuple[str, str, str]] = {
    # key → (角色, SMILES, 中文名)
    "TFPT": ("aldehyde",
             "O=Cc1ccc(-c2nc(-c3ccc(C=O)cc3)nc(-c3ccc(C=O)cc3)n2)cc1",
             "TFPT 三嗪三醛"),
    "443922-06-3": ("aldehyde",
                    "O=Cc1ccc(-c2nc(-c3ccc(C=O)cc3)nc(-c3ccc(C=O)cc3)n2)cc1",
                    "TFPT 三嗪三醛"),
    "TFPB": ("aldehyde",
             "O=Cc1ccc(-c2cc(-c3ccc(C=O)cc3)cc(-c3ccc(C=O)cc3)c2)cc1",
             "TFPB 三(4-甲酰苯基)苯"),
    "118688-53-2": ("aldehyde",
                    "O=Cc1ccc(-c2cc(-c3ccc(C=O)cc3)cc(-c3ccc(C=O)cc3)c2)cc1",
                    "TFPB 三(4-甲酰苯基)苯"),
    "B5": ("amine",
           "Nc1ccc(C(F)(F)F)cc1-c1ccc(N)cc1C(F)(F)F",
           "B5/TFMB 2,2'-双(三氟甲基)联苯二胺"),
    "TFMB": ("amine",
             "Nc1ccc(C(F)(F)F)cc1-c1ccc(N)cc1C(F)(F)F",
             "B5/TFMB 2,2'-双(三氟甲基)联苯二胺"),
    "341-58-2": ("amine",
                 "Nc1ccc(C(F)(F)F)cc1-c1ccc(N)cc1C(F)(F)F",
                 "B5/TFMB 2,2'-双(三氟甲基)联苯二胺"),
    "BD-CF3": ("amine",
               "Nc1ccc(C(F)(F)F)cc1-c1ccc(N)cc1C(F)(F)F",
               "B5/TFMB 2,2'-双(三氟甲基)联苯二胺"),
    "B6": ("amine",
           "Nc1ccc(C(F)(F)F)c(N)c1",
           "B6/PDA-CF3 2,5-二氨基三氟甲苯"),
    "PDA-CF3": ("amine",
                "Nc1ccc(C(F)(F)F)c(N)c1",
                "B6/PDA-CF3 2,5-二氨基三氟甲苯"),
}

# 成膜阳性/阴性模式（仅对「实验现象」部分判定，避免溶解度描述误判）
_POS_RE = re.compile(
    r"(形成|出现|观察到|得到|生成).{0,24}(薄膜|膜)|"
    r"(壁上|管壁).{0,16}(薄膜|膜)|连续.{0,6}光滑.{0,6}(薄膜|膜)|"
    r"薄膜.{0,12}(形成|连续)")
_NEG_RE = re.compile(
    r"未成膜|不成膜|无膜|未观察到.{0,12}(薄膜|膜)|未形成.{0,12}(薄膜|膜)|"
    r"固体颗粒堆积|粗糙的.{0,10}固体|不连续.{0,12}固体")


def _validate_monomers() -> None:
    for key, (role, smi, name) in MONOMERS.items():
        mol = Chem.MolFromSmiles(smi)
        if mol is None:
            raise SystemExit(f"[失败] {key}（{name}）SMILES 无法解析: {smi}")
    print(f"单体映射校验通过：{len(MONOMERS)} 个 key")


def _contains(text: str, key: str) -> bool:
    return key in text


def _film_verdict(text: str) -> int | None:
    """按「实验现象」段落判定档位：1 成膜 / 0 未成膜 / None 无结论。

    优先取「实验现象：」之后的部分（协议部分含溶解度描述会误判）。
    """
    marker = "实验现象"
    zone = text.split(marker, 1)[1] if marker in text else text
    if _POS_RE.search(zone):
        return 1
    if _NEG_RE.search(zone):
        return 0
    return None


def main() -> None:
    _validate_monomers()
    data = json.loads(EXTRACTED.read_text(encoding="utf-8"))
    seen: set[tuple[str, str]] = set()
    rows: list[dict] = []
    for doc_name, doc in data.items():
        for para in doc.get("paragraphs") or []:
            verdict = _film_verdict(para)
            if verdict is None:
                continue
            alds = [MONOMERS[k] for k in MONOMERS
                    if MONOMERS[k][0] == "aldehyde" and _contains(para, k)]
            amines = [MONOMERS[k] for k in MONOMERS
                      if MONOMERS[k][0] == "amine" and _contains(para, k)]
            # 去重：同段多个 key 映射同一单体
            alds = list({smi: (role, smi, name) for role, smi, name in alds}.values())
            amines = list({smi: (role, smi, name) for role, smi, name in amines}.values())
            if not alds or not amines:
                continue
            for _r, a_smi, a_name in alds:
                for _r2, b_smi, b_name in amines:
                    key = (a_smi, b_smi)
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append({
                        "aldehyde_smiles": a_smi,
                        "amine_smiles": b_smi,
                        "is_film": str(verdict),
                        "note": f"{a_name} + {b_name}；{doc_name[:30]}",
                        "evidence": para.strip()[:200],
                    })
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "aldehyde_smiles", "amine_smiles", "is_film", "note", "evidence"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n提取候选反馈 {len(rows)} 条 → {OUT_CSV}")
    for r in rows:
        print(f"  [{r['is_film']}] {r['note']}")
        print(f"      {r['aldehyde_smiles']} + {r['amine_smiles']}")
        print(f"      证据: {r['evidence'][:120]}")
    print("\n请用户逐条复核后，经设置页「GNN 模型演进 → 导入实验 CSV」"
          "（或本脚本生成的 CSV 手动核对后转正）进入反馈队列。")


if __name__ == "__main__":
    main()
