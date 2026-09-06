"""科研知识库图谱历史导入（v1.9.2）：把随包图谱反应节点导入为结构化条目。

幂等：可重复执行，已导入的（paper_id, group_id, kind）跳过。

用法：
    E:\\ANACONDA\\python.exe scripts/import_graph_to_entries.py [--limit N]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from literature import knowledge  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="只导入前 N 个反应节点（调试用）")
    args = ap.parse_args()
    stats = knowledge.import_from_graph(limit=args.limit)
    print("== 图谱历史导入完成 ==")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
