"""图谱导出（v1.9.8 C5）：JSON Canvas / GraphML / 离线可交互 HTML。

## 为什么这样设计（来自调研的两条结论）

1. **不发明格式，落到成熟宿主格式**：citation-atlas 输出 Obsidian 原生 **JSON Canvas 1.0**
   （免费获得拖拽/聚类/涂色），zotero-mcp 输出 **D3 HTML**，scribl 输出 **Cypher/GraphML**。
   我们三种都给，且都不引入新依赖。
2. **防爆炸**：外部/大图只取**一层深**并设**上限**（citation-atlas 的「外部被引一层深、
   默认上限 60」）。这里 `depth` 默认 1、`max_nodes` 默认 200，超出即截断并在返回里说明。

HTML 导出**完全离线自包含**（不引 CDN、不需要网络），因为本软件是桌面单机场景。
"""

from __future__ import annotations

import html
import json
import logging

logger = logging.getLogger(__name__)

DEFAULT_MAX_NODES = 200
REL_PREDICATES = ("cites", "similar_to")


def _nodes_edges(conn, *, paper_id: str | None = None, depth: int = 1,
                 max_nodes: int = DEFAULT_MAX_NODES,
                 predicates: tuple[str, ...] = REL_PREDICATES) -> dict:
    """取要导出的子图（paper↔paper 关系；可选从某篇出发做 N 跳邻域）。"""
    marks = ",".join("?" * len(predicates))
    selected: list[str] = []
    if paper_id:
        frontier = {f"paper:{paper_id}"}
        seen = set(frontier)
        for _ in range(max(1, int(depth))):
            nxt: set[str] = set()
            for node in frontier:
                for r in conn.execute(
                        f"SELECT subject,object FROM graph_edges WHERE predicate IN ({marks})"
                        f" AND (subject=? OR object=?)", [*predicates, node, node]):
                    for cand in (str(r["subject"]), str(r["object"])):
                        if cand.startswith("paper:") and cand not in seen:
                            nxt.add(cand)
            seen |= nxt
            frontier = nxt
            if not frontier:
                break
        selected = sorted(seen)
    if not selected:
        rows = conn.execute(
            f"SELECT subject,object FROM graph_edges WHERE predicate IN ({marks})"
            f" AND subject LIKE 'paper:%' AND object LIKE 'paper:%'", list(predicates))
        pool: list[str] = []
        for r in rows:
            for n in (str(r["subject"]), str(r["object"])):
                if n not in pool:
                    pool.append(n)
        selected = pool

    truncated = len(selected) > max_nodes
    selected = selected[:max_nodes]
    keep = set(selected)
    edges = []
    for r in conn.execute(
            f"SELECT subject,predicate,object,source,confidence FROM graph_edges"
            f" WHERE predicate IN ({marks})", list(predicates)):
        s, o = str(r["subject"]), str(r["object"])
        if s in keep and o in keep:
            edges.append({"from": s, "to": o, "predicate": r["predicate"],
                          "source": r["source"] or "",
                          "confidence": r["confidence"]})
    meta: dict[str, dict] = {}
    ids = [n.split(":", 1)[1] for n in selected]
    if ids:
        q = ",".join("?" * len(ids))
        for r in conn.execute(
                f"SELECT paper_id,title,journal,year,doi FROM items WHERE paper_id IN ({q})",
                ids):
            meta[str(r["paper_id"])] = {"title": r["title"] or "",
                                        "journal": r["journal"] or "",
                                        "year": r["year"], "doi": r["doi"] or ""}
    nodes = []
    for n in selected:
        pid = n.split(":", 1)[1]
        m = meta.get(pid, {})
        nodes.append({"id": n, "paper_id": pid, "label": m.get("title") or pid,
                      "journal": m.get("journal") or "", "year": m.get("year"),
                      "doi": m.get("doi") or ""})
    return {"nodes": nodes, "edges": edges, "truncated": truncated,
            "total_candidates": len(selected) + (0 if not truncated else 0),
            "seed": f"paper:{paper_id}" if paper_id else None}


# ---------------------------------------------------------------- JSON Canvas

def to_json_canvas(conn, *, paper_id: str | None = None, depth: int = 1,
                   max_nodes: int = DEFAULT_MAX_NODES,
                   color: str = "4") -> dict:
    """Obsidian **JSON Canvas 1.0**（nodes/edges，网格布局，可直接拖拽编辑）。"""
    sub = _nodes_edges(conn, paper_id=paper_id, depth=depth, max_nodes=max_nodes)
    per_row = 4
    nodes = []
    for i, n in enumerate(sub["nodes"]):
        nodes.append({
            "id": n["id"], "type": "text", "x": (i % per_row) * 320,
            "y": (i // per_row) * 200, "width": 300, "height": 160,
            "color": color,
            "text": (f"**{n['label']}**\n\n#{n['paper_id']}"
                     + (f" · {n['journal']}" if n["journal"] else "")
                     + (f" · {n['year']}" if n["year"] else "")
                     + (f"\n\n{n['doi']}" if n["doi"] else "")),
        })
    edges = [{"id": f"e{i}", "fromNode": e["from"], "toNode": e["to"],
              "label": ("引用" if e["predicate"] == "cites" else "相似"),
              "color": "1" if e["predicate"] == "cites" else "6"}
             for i, e in enumerate(sub["edges"])]
    return {"canvas": {"nodes": nodes, "edges": edges},
            "counts": {"nodes": len(nodes), "edges": len(edges)},
            "truncated": sub["truncated"], "seed": sub["seed"]}


# ---------------------------------------------------------------- GraphML

def to_graphml(conn, *, paper_id: str | None = None, depth: int = 1,
               max_nodes: int = DEFAULT_MAX_NODES) -> str:
    """GraphML XML（Gephi / yEd / Cytoscape 可直接打开）。手写以零依赖。"""
    sub = _nodes_edges(conn, paper_id=paper_id, depth=depth, max_nodes=max_nodes)
    esc = html.escape
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
           '<key id="label" for="node" attr.name="label" attr.type="string"/>',
           '<key id="year" for="node" attr.name="year" attr.type="int"/>',
           '<key id="predicate" for="edge" attr.name="predicate" attr.type="string"/>',
           '<key id="source" for="edge" attr.name="source" attr.type="string"/>',
           '<graph edgedefault="undirected">']
    for n in sub["nodes"]:
        out.append(f'<node id="{esc(n["id"])}">'
                   f'<data key="label">{esc(n["label"])}</data>'
                   + (f'<data key="year">{int(n["year"])}</data>' if n["year"] else "")
                   + '</node>')
    for i, e in enumerate(sub["edges"]):
        out.append(f'<edge id="e{i}" source="{esc(e["from"])}" target="{esc(e["to"])}">'
                   f'<data key="predicate">{esc(e["predicate"])}</data>'
                   f'<data key="source">{esc(e["source"])}</data></edge>')
    out += ["</graph>", "</graphml>"]
    return "\n".join(out)


# ---------------------------------------------------------------- 离线 HTML

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>COF 文献关联图谱</title>
<style>
 body{margin:0;font:13px/1.5 system-ui,"Microsoft YaHei",sans-serif;background:#0f1115;color:#e6e6e6}
 #bar{padding:8px 12px;border-bottom:1px solid #2a2f3a;display:flex;gap:12px;align-items:center}
 #bar b{color:#7cc4ff} canvas{display:block}
 .legend span{margin-right:10px}
 .dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px}
</style></head><body>
<div id="bar"><b>COF 文献关联图谱</b>
 <span>节点 __NODES__ · 边 __EDGES__ __TRUNC__</span>
 <span class="legend"><span><i class="dot" style="background:#7cc4ff"></i>节点</span>
 <span><i class="dot" style="background:#ffd166"></i>引用</span>
 <span><i class="dot" style="background:#8ce99a"></i>相似(算法推荐)</span></span>
 <span style="color:#8a93a5">拖拽节点 · 滚轮缩放 · 悬停看标题 · 完全离线</span></div>
<canvas id="c"></canvas>
<script>
const DATA = __DATA__;
const cv = document.getElementById('c'), ctx = cv.getContext('2d');
let W = cv.width = innerWidth, H = cv.height = innerHeight - 42;
const nodes = DATA.nodes.map((n,i)=>({...n, x: W/2 + 220*Math.cos(i*2.399), y: H/2 + 220*Math.sin(i*2.399), vx:0, vy:0}));
const byId = Object.fromEntries(nodes.map(n=>[n.id,n]));
const edges = DATA.edges.map(e=>({...e, s:byId[e.from], t:byId[e.to]})).filter(e=>e.s&&e.t);
let scale = 1, ox = 0, oy = 0, drag = null, hover = null;
function tick(){
  for (const n of nodes){ n.vx *= 0.85; n.vy *= 0.85; }
  for (const e of edges){
    const dx = e.t.x - e.s.x, dy = e.t.y - e.s.y, d = Math.hypot(dx,dy) || 1;
    const f = (d - 150) * 0.002 * (e.predicate === 'cites' ? 1.2 : 0.8);
    e.s.vx += dx/d*f; e.s.vy += dy/d*f; e.t.vx -= dx/d*f; e.t.vy -= dy/d*f;
  }
  for (let i=0;i<nodes.length;i++) for (let j=i+1;j<nodes.length;j++){
    const a=nodes[i], b=nodes[j], dx=b.x-a.x, dy=b.y-a.y; let d2=dx*dx+dy*dy||1;
    if (d2 < 62500){ const d=Math.sqrt(d2), f=900/d2; a.vx-=dx/d*f; a.vy-=dy/d*f; b.vx+=dx/d*f; b.vy+=dy/d*f; }
  }
  for (const n of nodes){ n.x += n.vx; n.y += n.vy; }
}
function draw(){
  ctx.setTransform(1,0,0,1,0,0); ctx.clearRect(0,0,W,H);
  ctx.translate(ox,oy); ctx.scale(scale,scale);
  ctx.lineWidth = 1/scale;
  for (const e of edges){
    ctx.strokeStyle = e.predicate === 'cites' ? 'rgba(255,209,102,.75)' : 'rgba(140,233,154,.5)';
    ctx.setLineDash(e.predicate === 'cites' ? [] : [4,4]);
    ctx.beginPath(); ctx.moveTo(e.s.x,e.s.y); ctx.lineTo(e.t.x,e.t.y); ctx.stroke();
  }
  ctx.setLineDash([]);
  for (const n of nodes){
    ctx.fillStyle = n === hover ? '#ffffff' : '#7cc4ff';
    ctx.beginPath(); ctx.arc(n.x,n.y,n === hover ? 8 : 5, 0, 6.2832); ctx.fill();
    if (scale > 0.75 || n === hover){
      ctx.fillStyle = '#c9d4e4'; ctx.font = `${11/scale}px system-ui`;
      ctx.fillText((n.label||'').slice(0,26), n.x + 8, n.y + 3);
    }
  }
}
function loop(){ tick(); draw(); requestAnimationFrame(loop); }
cv.addEventListener('mousemove', ev=>{
  const x = (ev.clientX-ox)/scale, y = (ev.clientY-oy)/scale;
  if (drag){ drag.x = x; drag.y = y; return; }
  hover = nodes.find(n=>Math.hypot(n.x-x,n.y-y)<9) || null;
});
cv.addEventListener('mousedown', ev=>{
  const x = (ev.clientX-ox)/scale, y = (ev.clientY-oy)/scale;
  drag = nodes.find(n=>Math.hypot(n.x-x,n.y-y)<9) || null;
});
addEventListener('mouseup', ()=> drag = null);
cv.addEventListener('wheel', ev=>{ ev.preventDefault(); scale = Math.min(3, Math.max(.25, scale*(ev.deltaY<0?1.1:.9))); }, {passive:false});
let panning = null;
cv.addEventListener('mousedown', ev=>{ if(!drag) panning = {x:ev.clientX-ox, y:ev.clientY-oy}; });
addEventListener('mousemove', ev=>{ if(panning && !drag){ ox = ev.clientX-panning.x; oy = ev.clientY-panning.y; } });
addEventListener('mouseup', ()=> panning = null);
loop();
</script></body></html>
"""


def to_html(conn, *, paper_id: str | None = None, depth: int = 1,
            max_nodes: int = DEFAULT_MAX_NODES) -> str:
    """**离线自包含**可交互 HTML（不引 CDN；拖拽/缩放/悬停）。"""
    sub = _nodes_edges(conn, paper_id=paper_id, depth=depth, max_nodes=max_nodes)
    data = json.dumps({"nodes": sub["nodes"], "edges": sub["edges"]},
                      ensure_ascii=False)
    return (_HTML_TEMPLATE
            .replace("__DATA__", data)
            .replace("__NODES__", str(len(sub["nodes"])))
            .replace("__EDGES__", str(len(sub["edges"])))
            .replace("__TRUNC__", "（已按上限截断）" if sub["truncated"] else ""))
