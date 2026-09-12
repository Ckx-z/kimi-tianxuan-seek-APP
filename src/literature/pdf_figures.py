"""PDF 文献图抽取（v1.9.4 方案 A）：补解析时自动获取文献里的图并入库文献图谱。

为什么需要：解析只读 PDF 文本层，图/谱图/结构式不会被保存；文献图谱此前只能手工上传。
本模块从 PDF 抽取：

1. **内嵌位图**：`page.get_images(full=True)` + `doc.extract_image(xref)`
   —— 覆盖照片、SEM/TEM 图、部分谱图（很多期刊谱图是矢量绘制，见第 2 条）；
2. **矢量图页渲染兜底**：若某页有图注（`Figure 3.` / `图 3` / `Scheme 1` / `Table 2`）
   但该页没有达到尺寸阈值的位图，则把「图注上方区域」按 DPI 渲染成 PNG
   （多数排版图在上、注在下；按图注 y 坐标向上取 ~60% 页高，避免整页噪音）；
3. **图注 + 归类**：把图注文本作为 caption，并按关键词自动判定
   `spectra`（PXRD/FTIR/NMR/UV/PL/XPS/TGA/吸附…）/ `structure`（结构式/合成路线/CIF…）/ 
   `mechanism`（其余），与 `figures.ALLOWED_TYPES` 对齐。

产出的「候选图」先落到暂存区（`literature/figure_staging/`），由前端在解析预览里勾选后再
调 `POST /{paper_id}/figures/import` 正式入图谱——保持「先审核后入库」的一致性，也避免把
整页渲染等噪音直接塞进图谱。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

try:
    from src import runtime_config
except ImportError:  # pragma: no cover
    import runtime_config  # type: ignore

logger = logging.getLogger(__name__)

STAGING_DIR = runtime_config.user_data_root() / "literature" / "figure_staging"

MIN_SIDE = 120                 # 位图最小边长（px）：过滤图标/公式碎片
MIN_PIXELS = 120 * 120         # 最小像素数
MAX_PER_PAPER = 40             # 单篇最多抽取的候选图数量
MAX_CANDIDATE_BYTES = 6 * 1024 * 1024   # 单图上限（超过多半是整页扫描底图）
PAGE_RENDER_DPI = 120          # 矢量图页渲染 DPI
MAX_PAGE_RENDERS = 12          # 单篇最多页渲染兜底数量
CAPTION_MAX_CHARS = 300
STAGING_TTL_HOURS = 24         # 暂存过期时间（下次抽取时顺带清理）

_CAPTION_RE = re.compile(
    r"^\s*(figure|fig\.?|scheme|table|chart|图|表)\s*([0-9]{1,2}[a-z]?)\b",
    re.IGNORECASE)

_SPECTRA_KEYS = (
    "pxrd", "xrd", "diffraction", "pawley", "refinement", "rietveld",
    "le bail", "ftir", "infrared", "ir spectrum", "raman",
    "nmr", "uv-vis", "uv/vis", "uv–vis", "uvvis", "absorption", "photoluminescence",
    "pl spectrum", "fluorescence", "xps", "tga", "thermogravimetric", "dsc",
    "adsorption", "desorption", "isotherm", "bet", "pore size", "pore-size",
    "n2 sorption", "cv curve", "eis", "impedance", "conductivity", "spectrum",
    "spectra", "spectroscopic",
)
_STRUCTURE_KEYS = (
    "structure", "structural formula", "schematic", "scheme", "synthetic route",
    "synthesis of", "crystal structure", "cif", "ortep", "topology", "linkage",
    "building unit", "molecular structure", "energy level diagram", "cartoon",
)
_MECHANISM_KEYS = (
    "mechanism", "formation", "growth", "interfacial", "reaction pathway",
    "proposed", "illustration", "model", "process",
)
HEADER_FOOTER_RATIO = 0.08   # 图片整体落在页眉/页脚 8% 区域内 → 视为 logo/装饰


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class FigureExtractError(Exception):
    """PDF 打开/抽取失败（调用方按「跳过抽取」处理，不阻塞解析）。"""


# ---------------------------------------------------------------- 图注 / 归类

def _page_captions(page) -> list[dict]:
    """页面里的图注块：[{text, y0, y1, label}]（按 y 升序）。"""
    out: list[dict] = []
    try:
        blocks = page.get_text("blocks") or []
    except Exception:  # pragma: no cover
        return out
    for block in blocks:
        if len(block) < 5:
            continue
        text = str(block[4] or "").strip()
        if not text:
            continue
        flat = " ".join(text.split())
        m = _CAPTION_RE.match(flat)
        if not m:
            continue
        label = f"{m.group(1).rstrip('.')} {m.group(2)}".replace("Figure", "Fig.") \
            if m.group(1).lower().startswith("fig") else \
            f"{m.group(1)} {m.group(2)}"
        out.append({
            "text": flat[:CAPTION_MAX_CHARS],
            "label": label,
            "y0": float(block[1]),
            "y1": float(block[3]),
        })
    out.sort(key=lambda c: c["y0"])
    return out


def _match_type(text_low: str) -> str:
    """关键词 → 类型（谱图优先：同一图注里既有光谱又有结构时，光谱更可核对）。"""
    if any(k in text_low for k in _SPECTRA_KEYS):
        return "spectra"
    if any(k in text_low for k in _STRUCTURE_KEYS):
        return "structure"
    if any(k in text_low for k in _MECHANISM_KEYS):
        return "mechanism"
    return ""


def classify_figure(caption: str, page_text: str = "") -> str:
    """图注 → figures.ALLOWED_TYPES 之一（structure / spectra / mechanism）。

    v1.9.4 调整：**以图注为主**判定；只有图注缺失或过短时才用页面文本弱判定
    （原实现把整页前 400 字掺进来，导致「Chemical structures of …」被页面上
    其它光谱词带偏成 spectra）。
    """
    cap = (caption or "").strip().lower()
    if cap:
        hit = _match_type(cap)
        if hit:
            return hit
        if len(cap) >= 20:      # 图注够长但无关键词 → 不借页面文本
            return "mechanism"
    return _match_type((page_text or "").lower()[:400]) or "mechanism"


def _pick_caption(captions: list[dict], y_bottom: float | None) -> dict | None:
    """选与图上/下最贴近的图注：优先图下方第一条，其次最近一条。"""
    if not captions:
        return None
    if y_bottom is not None:
        below = [c for c in captions if c["y0"] >= y_bottom - 8]
        if below:
            return below[0]
    return min(captions, key=lambda c: abs(c["y0"] - (y_bottom or 0)))


# ---------------------------------------------------------------- 抽取

def extract_candidates(data: bytes | None = None,
                       path: Path | None = None) -> list[dict]:
    """PDF → 候选图列表。

    每项：{kind: 'embedded'|'page_render', ext, data, caption, figure_type,
    page, width, height, sha1}
    失败抛 FigureExtractError（调用方自行降级为「本次无图」）。
    """
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover
        raise FigureExtractError("PyMuPDF 不可用") from exc
    try:
        doc = fitz.open(stream=data, filetype="pdf") if data is not None \
            else fitz.open(str(path))
    except Exception as exc:
        raise FigureExtractError(f"PDF 打开失败：{type(exc).__name__}") from exc

    out: list[dict] = []
    seen: set[str] = set()
    page_renders = 0
    try:
        for pno in range(doc.page_count):
            page = doc[pno]
            captions = _page_captions(page)
            page_text = ""
            try:
                page_text = page.get_text() or ""
            except Exception:  # pragma: no cover
                pass
            found_on_page = 0

            # 1) 内嵌位图
            try:
                images = page.get_images(full=True) or []
            except Exception:  # pragma: no cover
                images = []
            for img in images:
                if len(out) >= MAX_PER_PAPER:
                    break
                xref = img[0]
                try:
                    info = doc.extract_image(xref) or {}
                except Exception:
                    continue
                blob = info.get("image") or b""
                ext = str(info.get("ext") or "png").lower()
                width = int(info.get("width") or 0)
                height = int(info.get("height") or 0)
                if not blob or ext not in ("png", "jpg", "jpeg", "webp"):
                    continue
                if min(width, height) < MIN_SIDE or width * height < MIN_PIXELS:
                    continue
                if len(blob) > MAX_CANDIDATE_BYTES:
                    continue
                sha = hashlib.sha1(blob).hexdigest()
                if sha in seen:
                    continue
                # 图片位置（用于选图注 + 页眉页脚过滤）：新版 PyMuPDF 支持 get_image_rects
                y_bottom = None
                rects = []
                try:
                    rects = page.get_image_rects(xref) or []
                    if rects:
                        y_bottom = float(max(r.y1 for r in rects))
                except Exception:
                    y_bottom = None
                # 页眉/页脚整块（logo、水印、页码条）直接丢弃
                if rects:
                    page_h = float(page.rect.height)
                    band = page_h * HEADER_FOOTER_RATIO
                    if all(float(r.y1) <= band or float(r.y0) >= page_h - band
                           for r in rects):
                        continue
                cap = _pick_caption(captions, y_bottom)
                seen.add(sha)
                out.append({
                    "kind": "embedded",
                    "ext": ".jpg" if ext in ("jpg", "jpeg") else f".{ext}",
                    "data": blob,
                    "caption": (cap or {}).get("text", ""),
                    "caption_label": (cap or {}).get("label", ""),
                    "figure_type": classify_figure((cap or {}).get("text", ""),
                                                   page_text),
                    "page": pno + 1,
                    "width": width,
                    "height": height,
                    "sha1": sha,
                })
                found_on_page += 1

            # 2) 矢量图页渲染兜底：有图注但本页没有可用位图
            if (not found_on_page and captions
                    and page_renders < MAX_PAGE_RENDERS
                    and len(out) < MAX_PER_PAPER):
                cap = captions[0]
                try:
                    rect = page.rect
                    top = max(0.0, cap["y0"] - 0.62 * rect.height)
                    clip = fitz.Rect(rect.x0, top, rect.x1,
                                     min(rect.y1, cap["y1"] + 10))
                    pix = page.get_pixmap(dpi=PAGE_RENDER_DPI, clip=clip)
                    blob = pix.tobytes("png")
                except Exception as exc:  # pragma: no cover
                    logger.info("页渲染兜底失败 p%s: %s", pno + 1, exc)
                    blob = b""
                if blob and len(blob) <= MAX_CANDIDATE_BYTES:
                    sha = hashlib.sha1(blob).hexdigest()
                    if sha not in seen:
                        seen.add(sha)
                        out.append({
                            "kind": "page_render",
                            "ext": ".png",
                            "data": blob,
                            "caption": cap["text"],
                            "caption_label": cap["label"],
                            "figure_type": classify_figure(cap["text"], page_text),
                            "page": pno + 1,
                            "width": pix.width if 'pix' in dir() else 0,
                            "height": pix.height if 'pix' in dir() else 0,
                            "sha1": sha,
                        })
                        page_renders += 1
    finally:
        doc.close()
    return out[:MAX_PER_PAPER]


# ---------------------------------------------------------------- 暂存区

def _staged_meta_path(staged_id: str) -> Path:
    return STAGING_DIR / f"{staged_id}.json"


def prune_staging(ttl_hours: int = STAGING_TTL_HOURS) -> int:
    """清理过期暂存（默认 24h）；返回删除数量。"""
    if not STAGING_DIR.is_dir():
        return 0
    cutoff = datetime.now().astimezone() - timedelta(hours=ttl_hours)
    removed = 0
    for meta_file in STAGING_DIR.glob("*.json"):
        try:
            if datetime.fromtimestamp(meta_file.stat().st_mtime).astimezone() < cutoff:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                for key in ("file",):
                    target = STAGING_DIR / str(meta.get(key) or "")
                    if target.is_file():
                        target.unlink()
                meta_file.unlink()
                removed += 1
        except Exception:  # pragma: no cover
            continue
    return removed


def stage_candidates(paper_id: str, candidates: list[dict]) -> list[dict]:
    """候选图落暂存区，返回带 staged_id / url 的元数据（不含二进制）。"""
    if not candidates:
        return []
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    prune_staging()
    staged: list[dict] = []
    for cand in candidates:
        staged_id = f"fs_{uuid.uuid4().hex[:12]}"
        fname = f"{staged_id}{cand['ext']}"
        try:
            (STAGING_DIR / fname).write_bytes(cand["data"])
        except OSError as exc:  # pragma: no cover
            logger.warning("候选图暂存失败：%s", exc)
            continue
        meta = {
            "staged_id": staged_id,
            "paper_id": str(paper_id),
            "file": fname,
            "kind": cand["kind"],
            "figure_type": cand["figure_type"],
            "caption": cand["caption"],
            "caption_label": cand["caption_label"],
            "page": cand["page"],
            "width": cand["width"],
            "height": cand["height"],
            "sha1": cand["sha1"],
            "size": len(cand["data"]),
            "created_at": _now(),
        }
        _staged_meta_path(staged_id).write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        staged.append({**{k: v for k, v in meta.items() if k != "file"},
                       "url": f"/api/literature/figure-staging/{staged_id}/file"})
    return staged


def get_staged(staged_id: str) -> tuple[Path, dict] | None:
    sid = str(staged_id or "").strip()
    if not re.fullmatch(r"fs_[0-9a-f]{12}", sid):
        return None
    meta_file = _staged_meta_path(sid)
    if not meta_file.is_file():
        return None
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except Exception:  # pragma: no cover
        return None
    path = STAGING_DIR / str(meta.get("file") or "")
    if not path.is_file():
        return None
    return path, meta


def discard_staged(staged_id: str) -> bool:
    hit = get_staged(staged_id)
    if hit is None:
        return False
    path, _meta = hit
    try:
        path.unlink()
        _staged_meta_path(str(staged_id)).unlink()
    except OSError:  # pragma: no cover
        return False
    return True


def import_staged(paper_id: str, staged_ids: list[str]) -> dict:
    """把暂存的候选图正式写入文献图谱（复用 figures.add_figure）。

    返回 {imported: [...], skipped: [{staged_id, reason}], figure_ids: [...]}。
    """
    try:
        from literature import figures as figures_mod
    except ImportError:  # pragma: no cover
        from src.literature import figures as figures_mod  # type: ignore

    imported: list[dict] = []
    skipped: list[dict] = []
    for sid in staged_ids or []:
        hit = get_staged(sid)
        if hit is None:
            skipped.append({"staged_id": sid, "reason": "暂存已过期或不存在"})
            continue
        path, meta = hit
        try:
            data = path.read_bytes()
            rec = figures_mod.add_figure(
                paper_id,
                figure_type=str(meta.get("figure_type") or "mechanism"),
                caption=str(meta.get("caption") or ""),
                tags=["自动抽取", "内嵌图" if meta.get("kind") == "embedded"
                      else "页渲染"],
                meta={
                    "source": "pdf_extract",
                    "kind": meta.get("kind"),
                    "page": meta.get("page"),
                    "caption_label": meta.get("caption_label"),
                    "width": meta.get("width"),
                    "height": meta.get("height"),
                },
                ext=str(path.suffix or ".png"),
                data=data,
            )
            imported.append(rec)
            discard_staged(sid)
        except Exception as exc:
            skipped.append({"staged_id": sid,
                            "reason": f"{type(exc).__name__}: {exc}"})
    return {"imported": imported, "skipped": skipped,
            "figure_ids": [r.get("fig_id") for r in imported]}
