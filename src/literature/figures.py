"""文献图谱（v1.7.0，需求三）：structure / spectra / mechanism 三类图谱。

存储（frozen 友好，全部落在可写用户目录）：
- 文件本体：user_data_root()/literature/figures/<fig_id>.<ext>
- 元数据：  user_data_root()/literature/figures_index.json
  [{"fig_id", "paper_id", "figure_type", "caption", "tags", "meta",
    "file", "mime", "size", "score_note", "created_at", "updated_at",
    "caption_source"}]
  `caption_source`（v2.0.1）语义与 library_db.figures 表一致：
  pdf=PDF 文本层抽取 / vision=视觉模型读图 / user=人工编辑 / none=无图注；
  老索引记录没有该字段时，读取处按「有图注→pdf，无图注→none」派生（不回写文件）。

structure 类支持 SMILES → RDKit 2D 结构图（SVG 主口径，无需 cairo；
PNG 仅当 cairo 可用时生成，失败不阻塞）。索引写入走临时文件原子替换。
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path

from rdkit import Chem
from rdkit.Chem import AllChem
from rdkit.Chem.Draw import rdMolDraw2D

try:
    from src import runtime_config
except ImportError:  # pragma: no cover
    import runtime_config  # type: ignore

try:
    from references import titles
except ImportError:  # 包路径导入（项目根在 sys.path 上）
    from src.references import titles  # type: ignore

logger = logging.getLogger(__name__)

FIGURES_DIR = runtime_config.user_data_root() / "literature" / "figures"
INDEX_PATH = runtime_config.user_data_root() / "literature" / "figures_index.json"

ALLOWED_TYPES = {"structure", "spectra", "mechanism"}
ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".svg", ".webp"}
#: v2.0.1：图注来源（与 library_db.ALLOWED_CAPTION_SOURCES 同一约定）
ALLOWED_CAPTION_SOURCES = ("pdf", "vision", "user", "none")
MAX_FIG_BYTES = 20 * 1024 * 1024  # 单图 ≤20MB
SVG_WIDTH, SVG_HEIGHT = 420, 300  # SMILES 2D 渲染尺寸

_lock = threading.Lock()
_cache: list[dict] | None = None


class FigureError(Exception):
    """图谱校验/写入失败（路由转 400）。"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_id() -> str:
    return f"fig_{uuid.uuid4().hex[:12]}"


def _load() -> list[dict]:
    global _cache
    if _cache is not None:
        return _cache
    if not INDEX_PATH.is_file():
        _cache = []
        return _cache
    try:
        obj = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        _cache = obj if isinstance(obj, list) else []
    except Exception as exc:
        logger.warning("图谱索引读取失败（按空表处理）: %s", exc)
        _cache = []
    return _cache


def _save(items: list[dict]) -> None:
    global _cache
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_PATH.with_name(INDEX_PATH.name + ".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    tmp.replace(INDEX_PATH)
    _cache = items


def _validate_paper(paper_id: str) -> None:
    if titles.resolve_entry(paper_id) is None:
        raise FigureError(f"文献不存在（paper_id={paper_id}），请先录入该文献")


def _validate_meta(figure_type: str, meta: dict) -> dict:
    meta = dict(meta or {})
    if figure_type == "structure":
        smiles = str(meta.get("smiles") or "").strip()
        # v1.9.4：PDF 自动抽取的结构图（Scheme/晶体结构截图）没有 SMILES，
        # 允许以 role=unknown 入库；人工上传仍强制要求可解析 SMILES。
        if not smiles and str(meta.get("source") or "") == "pdf_extract":
            meta["role"] = "unknown"
            return meta
        mol = Chem.MolFromSmiles(smiles)
        if not smiles or mol is None:
            raise FigureError("structure 类型必须提供可解析的 smiles 元数据")
        meta["smiles"] = smiles
        # 自动判定单体角色（供「导入成膜打分」预填醛/胺槽位）
        n_ald = len(mol.GetSubstructMatches(Chem.MolFromSmarts("[CX3H](=O)")))
        n_amine = (len(mol.GetSubstructMatches(
                       Chem.MolFromSmarts("[NX3H2;!$(N[C,S]=O);!$(NO);!$(N=O)]")))
                   + len(mol.GetSubstructMatches(
                       Chem.MolFromSmarts("[NX3H1;!$(N[C,S]=O);!$(NO);!$(N=O)]([#6])[#6]"))))
        if n_ald > 0 and n_amine == 0:
            meta["role"] = "aldehyde"
        elif n_amine > 0 and n_ald == 0:
            meta["role"] = "amine"
        else:
            meta["role"] = "unknown"
    return meta


def render_structure_svg(smiles: str) -> str:
    """SMILES → 2D 结构图 SVG 文本（无 cairo 依赖）。解析失败抛 FigureError。"""
    mol = Chem.MolFromSmiles((smiles or "").strip())
    if mol is None:
        raise FigureError(f"SMILES 无法解析: {smiles}")
    AllChem.Compute2DCoords(mol)
    drawer = rdMolDraw2D.MolDraw2DSVG(SVG_WIDTH, SVG_HEIGHT)
    rdMolDraw2D.PrepareAndDrawMolecule(drawer, mol)
    drawer.FinishDrawing()
    return drawer.GetDrawingText()


def render_structure_png(smiles: str) -> bytes | None:
    """SMILES → PNG（可选能力）：cairo 不可用时返回 None，调用方回退 SVG。"""
    try:
        from rdkit.Chem.Draw import rdMolDraw2D as _d
        mol = Chem.MolFromSmiles((smiles or "").strip())
        if mol is None:
            return None
        AllChem.Compute2DCoords(mol)
        drawer = _d.MolDraw2DCairo(SVG_WIDTH, SVG_HEIGHT)
        _d.PrepareAndDrawMolecule(drawer, mol)
        drawer.FinishDrawing()
        return drawer.GetDrawingText()
    except Exception:  # pragma: no cover
        return None


def _default_caption_source(rec: dict) -> str:
    """老记录没有 caption_source 时按语义派生（有图注→pdf，否则 none）。"""
    src = str(rec.get("caption_source") or "").strip().lower()
    if src in ALLOWED_CAPTION_SOURCES:
        return src
    return "pdf" if str(rec.get("caption") or "").strip() else "none"


def add_figure(paper_id: str, figure_type: str, caption: str,
               tags: list[str], meta: dict, ext: str, data: bytes,
               score_note: str | None = None) -> dict:
    """新增图谱（上传图）：存文件 + 写索引，返回元数据。"""
    figure_type = (figure_type or "").strip().lower()
    if figure_type not in ALLOWED_TYPES:
        raise FigureError(f"figure_type 必须是 {sorted(ALLOWED_TYPES)} 之一")
    ext = (ext or "").lower()
    if ext not in ALLOWED_EXTS:
        raise FigureError(f"仅支持图片格式 {sorted(ALLOWED_EXTS)}")
    if not data:
        raise FigureError("上传文件为空")
    if len(data) > MAX_FIG_BYTES:
        raise FigureError(f"图片超过 {MAX_FIG_BYTES // (1024 * 1024)}MB 上限")
    _validate_paper(paper_id)
    meta = _validate_meta(figure_type, meta)

    fig_id = _new_id()
    fname = f"{fig_id}{ext}"
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    (FIGURES_DIR / fname).write_bytes(data)

    caption = (caption or "").strip()
    # v2.0.1：图注来源 —— PDF 自动抽取（pdf_figures.import_staged）→ pdf；
    # 人工上传且填了图注 → user；无图注 → none
    caption_source = ("pdf" if str(meta.get("source") or "") == "pdf_extract"
                      else "user") if caption else "none"
    rec = {
        "fig_id": fig_id,
        "paper_id": str(paper_id).strip(),
        "figure_type": figure_type,
        "caption": caption,
        "caption_source": caption_source,
        "tags": [str(t).strip() for t in (tags or []) if str(t).strip()],
        "meta": meta,
        "file": fname,
        "mime": {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                 "svg": "image/svg+xml", "webp": "image/webp"}[ext[1:]],
        "size": len(data),
        "score_note": (score_note or "").strip() or None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    with _lock:
        items = list(_load())
        items.append(rec)
        _save(items)
    return dict(rec)


def add_structure_from_smiles(paper_id: str, smiles: str,
                              caption: str) -> dict:
    """SMILES → 2D 结构图（SVG 主口径；PNG 可用则用 PNG）入库。"""
    _validate_paper(paper_id)
    smiles = (smiles or "").strip()
    if Chem.MolFromSmiles(smiles) is None:
        raise FigureError(f"SMILES 无法解析: {smiles}")
    png = render_structure_png(smiles)
    if png is not None:
        return add_figure(paper_id, "structure", caption, ["结构图"],
                          {"smiles": smiles}, ".png", png)
    svg = render_structure_svg(smiles).encode("utf-8")
    return add_figure(paper_id, "structure", caption, ["结构图"],
                      {"smiles": smiles}, ".svg", svg)


def list_figures(paper_id: str | None = None,
                 figure_type: str | None = None,
                 tag: str | None = None) -> list[dict]:
    """筛选图谱（paper_id / 类型 / 标签），按 created_at 倒序。"""
    out = []
    for rec in _load():
        if paper_id and rec.get("paper_id") != str(paper_id):
            continue
        if figure_type and rec.get("figure_type") != figure_type:
            continue
        if tag and tag not in (rec.get("tags") or []):
            continue
        item = dict(rec)
        item["caption_source"] = _default_caption_source(item)
        out.append(item)
    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return out


def get_figure(fig_id: str) -> dict | None:
    for rec in _load():
        if rec.get("fig_id") == fig_id:
            item = dict(rec)
            item["caption_source"] = _default_caption_source(item)
            return item
    return None


def figure_file_path(rec: dict) -> Path:
    return FIGURES_DIR / str(rec.get("file") or "")


def resolve_figure_file(rec: dict | str) -> Path | None:
    """图谱记录 → 实际可读文件路径；找不到返回 None。

    `file` 列的真实语义（v2.0.1 核实）：**裸文件名**（如 `fig_1a2b3c4d5e6f.png`），
    由 `add_figure` 写入 `FIGURES_DIR = user_data_root()/literature/figures/`；
    `library_db` 迁移时也是**原样搬运** figures_index.json 的 `file` 字段。
    为兼容历史/外部数据（绝对路径、带子目录的相对路径、暂存区文件），
    这里按候选根逐一探测，全部不存在才返回 None（调用方据此报"文件缺失"）。
    """
    name = str((rec.get("file") if isinstance(rec, dict) else rec) or "").strip()
    if not name:
        return None
    raw = Path(name)
    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.append(FIGURES_DIR / name)
        try:
            root = runtime_config.user_data_root() / "literature"
            candidates += [root / "figures" / name, root / name,
                           root / "figure_staging" / name]
        except Exception:  # pragma: no cover - 路径解析异常按"找不到"处理
            pass
    for cand in candidates:
        try:
            if cand.is_file():
                return cand
        except OSError:  # pragma: no cover
            continue
    return None


def update_figure(fig_id: str, caption: str | None = None,
                  tags: list[str] | None = None, meta: dict | None = None,
                  score_note: str | None = None,
                  caption_source: str | None = None) -> dict | None:
    """更新标注（caption/tags/meta/score_note/caption_source）；至少改一项。

    `caption_source`（v2.0.1）：显式传入时必须合法；只改 caption（且**文本确实变了**）
    而未传 source 时视为**人工编辑**（source=user），避免把视觉/PDF 标注与人工修改混淆；
    只改标签/备注（图注文本不变）时保留原来源。
    """
    if caption_source is not None and \
            str(caption_source).strip().lower() not in ALLOWED_CAPTION_SOURCES:
        raise FigureError(
            f"caption_source 必须是 {list(ALLOWED_CAPTION_SOURCES)} 之一")
    with _lock:
        items = list(_load())
        for rec in items:
            if rec.get("fig_id") != fig_id:
                continue
            if caption is not None:
                new_caption = (caption or "").strip()
                # 图注**文本真的变了**才算人工编辑（只改标签/备注时不夺走原来源）
                if caption_source is None and \
                        new_caption != str(rec.get("caption") or "").strip():
                    rec["caption_source"] = "user"
                rec["caption"] = new_caption
            if caption_source is not None:
                rec["caption_source"] = str(caption_source).strip().lower()
            if tags is not None:
                rec["tags"] = [str(t).strip() for t in tags if str(t).strip()]
            if meta is not None:
                rec["meta"] = _validate_meta(rec.get("figure_type") or "", meta)
            if score_note is not None:
                rec["score_note"] = (score_note or "").strip() or None
            rec["updated_at"] = _now()
            _save(items)
            return dict(rec)
    return None


def delete_figure(fig_id: str) -> bool:
    """删除图谱：文件 + 索引条目同步移除（无孤儿文件）。"""
    with _lock:
        items = list(_load())
        target = next((r for r in items if r.get("fig_id") == fig_id), None)
        if target is None:
            return False
        try:
            p = figure_file_path(target)
            if p.is_file():
                p.unlink()
        except Exception as exc:  # 文件删不掉则回滚索引，避免孤儿索引
            logger.warning("图谱文件删除失败（索引未动）: %s", exc)
            return False
        _save([r for r in items if r.get("fig_id") != fig_id])
        return True
