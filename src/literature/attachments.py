"""文献附件（v1.9.3 问题 2）：主文 PDF + 补充信息（SI）PDF 的留存与复用。

背景：补解析原先只收单个 `UploadFile` 且**读完即丢**，SI（Supporting
Information，含大量数据/图表/表征）无处上传、无法复用、无法回看。本模块提供：

- 存储：`user_data_root()/literature/pdfs/<paper_id>/<sha1[:12]>_<safe_name>.pdf`
  —— 内容 sha1 去重：同一文献重复上传同一文件不重复占盘（返回已有记录）；
- 索引：`user_data_root()/literature/pdfs_index.jsonl`（一行一附件）；
- 角色：`main`（主文）/ `si`（补充信息），解析时主文在前、SI 依次追加；
- 约束：仅 PDF、单文件 ≤20MB、单文献 ≤8 个附件；
- 文本：fitz 提取（无文本层时如实报 chars=0，由上层给「疑似扫描件」提示）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

try:
    from src import runtime_config
except ImportError:  # pragma: no cover
    import runtime_config  # type: ignore

logger = logging.getLogger(__name__)

PDFS_DIR = runtime_config.user_data_root() / "literature" / "pdfs"
INDEX_PATH = runtime_config.user_data_root() / "literature" / "pdfs_index.jsonl"

MAX_PDF_BYTES = 20 * 1024 * 1024     # 单文件 ≤20MB
MAX_PDFS_PER_PAPER = 8               # 单文献 ≤8 个附件（主文 + 多份 SI）
MIN_PDF_CHARS_PER_PAGE = 50          # 低于此值视为无文本层（扫描件）
ALLOWED_ROLES = ("main", "si")
_ID_RE = re.compile(r"^pf_[0-9a-f]{12}$")
_SAFE_NAME_RE = re.compile(r"[^0-9A-Za-z._\u4e00-\u9fff-]+")

_lock = threading.Lock()


class AttachmentError(Exception):
    """附件校验/写入失败（路由转 400）。"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_id() -> str:
    return f"pf_{uuid.uuid4().hex[:12]}"


def _safe_name(filename: str) -> str:
    name = Path(str(filename or "")).name.strip() or "document.pdf"
    cleaned = _SAFE_NAME_RE.sub("_", name)
    cleaned = cleaned.strip("._") or "document"
    if not cleaned.lower().endswith(".pdf"):
        cleaned += ".pdf"
    return cleaned[:120]


def _load() -> list[dict]:
    out: list[dict] = []
    if not INDEX_PATH.is_file():
        return out
    try:
        for line in INDEX_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict) and obj.get("file_id"):
                out.append(obj)
    except Exception as exc:  # pragma: no cover
        logger.warning("文献附件索引读取失败（按空表处理）: %s", exc)
        return []
    return out


def _save(items: list[dict]) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_PATH.with_name(INDEX_PATH.name + ".tmp")
    tmp.write_text(
        "\n".join(json.dumps(i, ensure_ascii=False) for i in items) + "\n",
        encoding="utf-8")
    tmp.replace(INDEX_PATH)


def _path_of(paper_id: str, file_id: str, filename: str) -> Path:
    return PDFS_DIR / str(paper_id) / f"{file_id}_{_safe_name(filename)}"


def list_pdfs(paper_id: str | None = None) -> list[dict]:
    """附件列表（可按 paper_id 过滤）；带 sha1/角色/页数字符数等元信息。"""
    items = _load()
    if paper_id is None:
        return items
    pid = str(paper_id)
    return [i for i in items if str(i.get("paper_id")) == pid]


def get_pdf(file_id: str) -> tuple[Path, dict] | None:
    fid = str(file_id or "").strip()
    if not _ID_RE.match(fid):
        return None
    for item in _load():
        if item.get("file_id") != fid:
            continue
        path = _path_of(item.get("paper_id"), fid, item.get("filename") or "")
        if not path.is_file():
            # 索引在、文件被外部删除：返回 None，由上层清理
            return None
        return path, item
    return None


def pdf_text(path: Path) -> tuple[str, int, int]:
    """PDF → (全文, 页数, 字符数)；打不开时抛 AttachmentError。"""
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover
        raise AttachmentError("PDF 解析组件不可用（PyMuPDF 未安装）") from exc
    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        raise AttachmentError(f"PDF 打开失败：{type(exc).__name__}") from exc
    try:
        text = "\n".join(page.get_text() for page in doc)
        return text, doc.page_count, len(text.strip())
    finally:
        doc.close()


def save_pdf(paper_id: str, filename: str, data: bytes,
             role: str = "main") -> dict:
    """保存附件（sha1 去重）。

    - 同文献 + 同 sha1 已存在 → 直接返回既有记录（`deduplicated=True`），
      不重复占盘；
    - 角色非法 / 非 PDF / 空文件 / 超限 / 超数量 → AttachmentError。
    """
    pid = str(paper_id or "").strip()
    if not pid:
        raise AttachmentError("paper_id 不能为空")
    role = (role or "main").strip().lower()
    if role not in ALLOWED_ROLES:
        raise AttachmentError(f"role 必须是 {'/'.join(ALLOWED_ROLES)}")
    if not data:
        raise AttachmentError("上传文件为空")
    if len(data) > MAX_PDF_BYTES:
        raise AttachmentError(
            f"PDF 超过 {MAX_PDF_BYTES // (1024 * 1024)}MB 上限，请压缩后重试")
    if not data[:5].startswith(b"%PDF"):
        raise AttachmentError("仅支持 PDF 文件（表头不是 %PDF）")
    sha1 = hashlib.sha1(data).hexdigest()
    name = _safe_name(filename)

    with _lock:
        items = _load()
        for item in items:
            if str(item.get("paper_id")) == pid \
                    and item.get("sha1") == sha1:
                return {**item, "deduplicated": True}
        existing = [i for i in items if str(i.get("paper_id")) == pid]
        if len(existing) >= MAX_PDFS_PER_PAPER:
            raise AttachmentError(
                f"该文献已有 {len(existing)} 个附件（上限 {MAX_PDFS_PER_PAPER}），"
                "请先删除不再需要的文件")
        file_id = _new_id()
        path = _path_of(pid, file_id, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        meta = {
            "file_id": file_id,
            "paper_id": pid,
            "filename": name,
            "role": role,
            "size": len(data),
            "sha1": sha1,
            "uploaded_at": _now(),
            "pages": 0,
            "chars": 0,
            "path": str(path),
        }
        # 立即统计页数/字符数（失败不阻塞上传，标 chars=0）
        try:
            text, pages, chars = pdf_text(path)
            meta["pages"] = pages
            meta["chars"] = chars
        except AttachmentError as exc:
            logger.warning("附件文本统计失败 %s: %s", name, exc)
        items.append(meta)
        _save(items)
        return {**meta, "deduplicated": False}


def update_meta(file_id: str, **fields) -> dict | None:
    """更新附件元信息（如 role 切换 主文/SI）。"""
    fid = str(file_id or "").strip()
    with _lock:
        items = _load()
        hit = None
        for item in items:
            if item.get("file_id") == fid:
                for key, value in fields.items():
                    if key in ("role",) and value not in ALLOWED_ROLES:
                        raise AttachmentError("role 必须是 main/si")
                    item[key] = value
                hit = dict(item)
                break
        if hit is None:
            return None
        _save(items)
        return hit


def delete_pdf(file_id: str) -> bool:
    """删除附件（索引 + 文件）；文件已被外部删除时也返回 True（索引清理成功）。"""
    fid = str(file_id or "").strip()
    if not _ID_RE.match(fid):
        return False
    with _lock:
        items = _load()
        keep = [i for i in items if i.get("file_id") != fid]
        if len(keep) == len(items):
            return False
        target = next(i for i in items if i.get("file_id") == fid)
        path = _path_of(target.get("paper_id"), fid, target.get("filename") or "")
        try:
            if path.is_file():
                path.unlink()
        except OSError as exc:  # pragma: no cover
            logger.warning("附件文件删除失败 %s: %s", path, exc)
        _save(keep)
        return True


def combined_text(paper_id: str, file_ids: list[str] | None = None,
                  max_chars: int = 400000) -> dict:
    """按「主文在前、SI 在后」拼接附件全文，供解析入库。

    返回 {"sources": [{file_id, filename, role, pages, chars}],
          "text": 拼接后全文（带分隔标记）, "scanned": [疑似扫描件的文件名]}
    """
    pid = str(paper_id or "").strip()
    items = list_pdfs(pid)
    if file_ids:
        wanted = {str(f) for f in file_ids}
        items = [i for i in items if i.get("file_id") in wanted]
    items.sort(key=lambda i: (0 if i.get("role") == "main" else 1,
                              str(i.get("uploaded_at") or "")))
    parts: list[str] = []
    sources: list[dict] = []
    scanned: list[str] = []
    si_index = 0
    total = 0
    for item in items:
        path = _path_of(pid, item.get("file_id"), item.get("filename") or "")
        if not path.is_file():
            continue
        try:
            text, pages, _chars = pdf_text(path)
        except AttachmentError as exc:
            logger.warning("附件读取失败 %s: %s", item.get("filename"), exc)
            continue
        if pages and len(text.strip()) / pages < MIN_PDF_CHARS_PER_PAGE:
            scanned.append(str(item.get("filename") or ""))
        role = item.get("role") or "main"
        if role == "main":
            marker = f"\n\n=== [主文] {item.get('filename')} ===\n"
        else:
            si_index += 1
            marker = f"\n\n=== [SI {si_index}] {item.get('filename')} ===\n"
        if total + len(text) > max_chars:
            text = text[:max(0, max_chars - total)]
        parts.append(marker + text)
        total += len(text)
        sources.append({
            "file_id": item.get("file_id"),
            "filename": item.get("filename"),
            "role": role,
            "pages": pages,
            "chars": len(text.strip()),
        })
        if total >= max_chars:
            break
    return {"sources": sources,
            "text": "".join(parts).strip(), "scanned": scanned}
