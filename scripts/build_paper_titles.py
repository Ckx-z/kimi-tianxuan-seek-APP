"""从旧项目结构化文献库批量构建 data/paper_titles.json。

来源（只读）：C:\\Users\\ckx\\Desktop\\tianxuan seek\\data\\structured_v2 与
structured_v3 的 YAML 头部字段 paper_id / title / doi。
（structured / structured_new / structured_new3 的 YAML 无 title/doi 字段，已核实。）

输出：data/paper_titles.json —— {paper_id: {"title": ..., "doi": ...}}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

OLD_ROOT = Path(r"C:\Users\ckx\Desktop\tianxuan seek\data")
SOURCES = [OLD_ROOT / "structured_v2", OLD_ROOT / "structured_v3"]
OUT = Path(__file__).resolve().parents[1] / "data" / "paper_titles.json"


def main() -> int:
    table: dict[str, dict] = {}
    skipped = 0
    for src in SOURCES:
        if not src.is_dir():
            print(f"[warn] 来源目录不存在，跳过: {src}")
            continue
        for p in sorted(src.glob("*.yaml")):
            try:
                obj = yaml.safe_load(p.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"[warn] YAML 解析失败 {p.name}: {exc}")
                skipped += 1
                continue
            if not isinstance(obj, dict):
                skipped += 1
                continue
            pid = str(obj.get("paper_id") or "").strip()
            title = str(obj.get("title") or "").strip()
            doi = str(obj.get("doi") or "").strip()
            if not pid or not title:
                skipped += 1
                continue
            if pid in table and table[pid]["title"] != title:
                print(f"[warn] paper_id 冲突 {pid}: {table[pid]['title']!r} vs {title!r}")
            table[pid] = {"title": title, "doi": doi}

    OUT.write_text(
        json.dumps(table, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"写入 {OUT}：{len(table)} 条（跳过 {skipped} 个文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
