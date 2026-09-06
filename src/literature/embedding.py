"""科研知识库 embedding（v1.9.0）：三态提供方（off / local / online）。

- off（默认）：不向量化，检索走图路径 + 文本匹配兜底；
- local：dphuanjing + sentence-transformers 加载本地模型（默认
  BAAI/bge-m3，1024 维，归一化后余弦=内积）；
  批量编码走子进程 worker（src/literature/_embed_worker.py），
  解释器与模型名来自文献解析设置（embedding_provider=local）；
- online：DashScope compatible-mode text-embedding-v4（key 独立配置）。

向量存储：user_data_root()/graphrag_user/literature_emb.jsonl
{entry_id, vector}；检索为线性内积 top-k（条目量级小，够用）。
"""

from __future__ import annotations

import json
import logging
import math
import subprocess
import urllib.request
import uuid
from pathlib import Path

try:
    from src import runtime_config
except ImportError:  # pragma: no cover
    import runtime_config  # type: ignore

logger = logging.getLogger(__name__)

EMB_PATH = runtime_config.user_data_root() / "graphrag_user" / "literature_emb.jsonl"
# worker 由 dphuanjing 以文件方式执行：源码态=本模块旁；frozen=随包 datas
_WORKER = runtime_config.resource_root() / "src" / "literature" / "_embed_worker.py"
_DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"


def _settings() -> dict:
    from literature import llm_extract
    return llm_extract._read_settings()


def provider() -> str:
    return str(_settings().get("embedding_provider") or "off").strip()


def status() -> dict:
    """当前提供方可用性（前端设置页展示）。"""
    p = provider()
    s = _settings()
    if p == "local":
        try:
            from src import runtime_config as rc
            py = rc.gnn_python()
            if py is None or not py.exists():
                return {"provider": p, "available": False,
                        "reason": "未找到 dphuanjing 推理环境"}
            model = str(s.get("embedding_model")
                        or "BAAI/bge-m3").strip()
            probe = subprocess.run(
                [str(py), "-c",
                 "import importlib.util as u; "
                 "print(u.find_spec('sentence_transformers') is not None)"],
                capture_output=True, timeout=60)
            if probe.returncode != 0 or b"True" not in probe.stdout:
                return {"provider": p, "available": False,
                        "reason": "dphuanjing 未安装 sentence_transformers："
                                  "运行 scripts/install_lit_embedding.bat"}
            return {"provider": p, "available": True, "model": model,
                    "reason": ""}
        except Exception as exc:
            return {"provider": p, "available": False,
                    "reason": f"探测失败：{type(exc).__name__}"}
    if p == "online":
        key = str(s.get("embedding_api_key") or "").strip()
        return {"provider": p, "available": bool(key),
                "reason": "" if key else "未配置在线 embedding key"}
    return {"provider": "off", "available": False,
            "reason": "向量化关闭：检索走图路径 + 文本匹配"}


def _embed_local(texts: list[str]) -> list[list[float]] | None:
    from src import runtime_config as rc
    py = rc.gnn_python()
    if py is None or not py.exists():
        return None
    s = _settings()
    model = str(s.get("embedding_model") or "BAAI/bge-m3")
    work = EMB_PATH.parent / f"_embed_in_{uuid.uuid4().hex[:8]}.jsonl"
    out = EMB_PATH.parent / f"_embed_out_{uuid.uuid4().hex[:8]}.jsonl"
    work.write_text("\n".join(json.dumps({"text": t}, ensure_ascii=False)
                              for t in texts), encoding="utf-8")
    try:
        r = subprocess.run(
            [str(py), str(_WORKER), "--model", model,
             "--in", str(work), "--out", str(out)],
            capture_output=True, timeout=1800)
        if r.returncode != 0 or not out.is_file():
            logger.warning("本地 embedding 失败: %s",
                           r.stderr.decode("utf-8", errors="replace")[-300:])
            return None
        vectors = []
        for line in out.read_text(encoding="utf-8").splitlines():
            obj = json.loads(line)
            if isinstance(obj.get("vector"), list):
                vectors.append([float(x) for x in obj["vector"]])
        return vectors if len(vectors) == len(texts) else None
    except Exception as exc:
        logger.warning("本地 embedding 异常: %s", exc)
        return None
    finally:
        for p in (work, out):
            try:
                p.unlink()
            except OSError:
                pass


def _embed_online(texts: list[str]) -> list[list[float]] | None:
    s = _settings()
    key = str(s.get("embedding_api_key") or "").strip()
    if not key:
        return None
    req = urllib.request.Request(
        _DASHSCOPE_URL,
        data=json.dumps({
            "model": "text-embedding-v4",
            "input": texts,
            "dimensions": 1024,
        }).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode("utf-8"))
        items = sorted(data.get("data") or [], key=lambda x: x.get("index", 0))
        return [list(item["embedding"]) for item in items]
    except Exception as exc:
        logger.warning("在线 embedding 失败: %s", exc)
        return None


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """按当前提供方编码；off/失败返回 None（调用方跳过向量化）。"""
    texts = [str(t or "") for t in texts]
    if not texts:
        return None
    p = provider()
    if p == "local":
        return _embed_local(texts)
    if p == "online":
        return _embed_online(texts)
    return None


# ---------------------------------------------------------------- 存储/检索

def _entry_text(entry: dict) -> str:
    """条目 → 编码文本（数值/结论/证据优先）。"""
    parts = [str(entry.get("kind") or ""),
             str(entry.get("experiment") or ""),
             str(entry.get("evidence") or "")]
    for m in (entry.get("metrics") or []):
        parts.append(f"{m.get('name')}={m.get('value')}{m.get('unit') or ''}")
    for k in ("conclusion", "technique", "sample", "property_name",
              "dft_method", "dft_target", "stoichiometry", "topology",
              "synthesis_method"):
        if entry.get(k):
            parts.append(str(entry[k]))
    if entry.get("conditions"):
        parts.append(json.dumps(entry["conditions"], ensure_ascii=False))
    return " ".join(parts)[:2000]


def _load() -> list[dict]:
    if not EMB_PATH.is_file():
        return []
    out = []
    for line in EMB_PATH.read_text(encoding="utf-8").splitlines():
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and obj.get("entry_id"):
                out.append(obj)
        except Exception:
            continue
    return out


def _save(rows: list[dict]) -> None:
    EMB_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = EMB_PATH.with_name(EMB_PATH.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(EMB_PATH)


def sync_entries(entries: list[dict]) -> int:
    """入库后同步向量（off/失败 → 返回 0，不阻塞）。"""
    if provider() == "off":
        return 0
    targets = [e for e in entries if e.get("entry_id")]
    vectors = embed_texts([_entry_text(e) for e in targets])
    if vectors is None:
        return 0
    rows = _load()
    by_id = {r["entry_id"]: r for r in rows}
    for e, vec in zip(targets, vectors):
        by_id[e["entry_id"]] = {"entry_id": e["entry_id"], "vector": vec}
    _save(list(by_id.values()))
    return len(targets)


def remove_entry(entry_id: str) -> bool:
    rows = [r for r in _load() if r.get("entry_id") != entry_id]
    if len(rows) == len(_load()):
        return False
    _save(rows)
    return True


def search(query: str, top_k: int = 5) -> list[dict]:
    """向量检索（内积 top-k）；off/失败返回空列表。"""
    vecs = embed_texts([query])
    if not vecs:
        return []
    q = vecs[0]
    norm_q = math.sqrt(sum(x * x for x in q)) or 1.0
    scored = []
    for r in _load():
        v = r.get("vector")
        if not isinstance(v, list) or len(v) != len(q):
            continue
        dot = sum(a * b for a, b in zip(q, v))
        nv = math.sqrt(sum(x * x for x in v)) or 1.0
        scored.append((dot / (norm_q * nv), r["entry_id"]))
    scored.sort(key=lambda x: -x[0])
    from literature import knowledge
    out = []
    for score, eid in scored[:top_k]:
        rec = knowledge.get_entry(eid)
        if rec is None:
            continue
        rec["_score"] = round(score, 4)
        out.append(rec)
    return out
