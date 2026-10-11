"""只读核对：figures_index.json（事实来源） vs library.db（镜像） vs 磁盘文件。

三者不一致会导致「图片列表有图但点开报错」或「AI 识别找不到文件」，
所以独立核对一遍（只读，不写任何东西）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(r"C:\Users\ckx\AppData\Roaming\COF-Film-Recommend\data\literature")
INDEX = ROOT / "figures_index.json"
DB = ROOT / "library.db"
FIGS = ROOT / "figures"

idx = json.loads(INDEX.read_text(encoding="utf-8"))
if isinstance(idx, dict):
    idx = idx.get("figures") or idx.get("items") or []
idx_files = {str(r.get("file") or "") for r in idx if isinstance(r, dict)}
idx_ids = {str(r.get("fig_id") or "") for r in idx if isinstance(r, dict)}

conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
db_files = {str(r["file"] or "") for r in conn.execute("SELECT file FROM figures")}
db_ids = {str(r["fig_id"] or "") for r in conn.execute("SELECT fig_id FROM figures")}
db_src = {}
for r in conn.execute("SELECT caption_source s, COUNT(*) c FROM figures GROUP BY 1"):
    db_src[r["s"]] = r["c"]
conn.close()

disk = {p.name for p in FIGS.iterdir() if p.is_file()}

print("index 条目 =", len(idx_ids), " db 行 =", len(db_ids), " 磁盘文件 =", len(disk))
print("index - db  (镜像缺) =", len(idx_ids - db_ids), sorted(idx_ids - db_ids)[:5])
print("db - index  (镜像多) =", len(db_ids - idx_ids), sorted(db_ids - idx_ids)[:5])
print("db 引用但磁盘缺   =", len(db_files - disk), sorted(db_files - disk)[:5])
print("磁盘多出（无引用） =", len(disk - db_files), sorted(disk - db_files)[:5])
print("caption_source 分布 =", db_src)

# 有图注的条目里，caption 非空比例（判断「空图注」是否被算成 pdf）
empty_pdf = 0
for r in conn_all if False else []:
    pass
conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
for r in conn.execute("SELECT caption, caption_source FROM figures"):
    if r["caption_source"] == "pdf" and not str(r["caption"] or "").strip():
        empty_pdf += 1
conn.close()
print("caption_source=pdf 但 caption 为空 =", empty_pdf)

# 各文献的图数量（用户验收时挑有图的文献）
conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
print("\n按文献统计（paper_id / 图数 / 其中已被视觉标注）:")
for r in conn.execute(
        "SELECT paper_id, COUNT(*) c, "
        "SUM(CASE WHEN caption_source='vision' THEN 1 ELSE 0 END) v "
        "FROM figures GROUP BY paper_id ORDER BY c DESC"):
    print(f"   {r['paper_id']:>6s}  {r['c']:>3d}  {r['v'] or 0}")
conn.close()
