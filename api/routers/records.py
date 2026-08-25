"""实验记录路由（含草稿暂存、时间线附件上传/下载/删除、Word 导出）。"""

from __future__ import annotations

import urllib.parse

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse

from .. import __version__
from ..schemas import RecordCreate, RecordsBundleExport, RecordUpdate

router = APIRouter(prefix="/api/records", tags=["records"])

# 附件类型扩展名 → 下载 Content-Type（图片内联预览，其余作为附件下载）
_MIME_BY_EXT = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv", ".txt": "text/plain", ".md": "text/markdown",
}


def _store():
    from records import store
    return store


@router.get("")
def list_records(favorite_id: str | None = None):
    return {"records": _store().list_records(favorite_id=favorite_id)}


@router.post("", status_code=201)
def create_record(req: RecordCreate):
    is_draft = req.status.strip() == "draft"
    if not is_draft and not req.experiment_no.strip():
        raise HTTPException(400, "experiment_no（实验编号）为必填")
    try:
        return _store().create_record(
            favorite_id=req.favorite_id or None,
            aldehyde_smiles=req.aldehyde_smiles.strip(),
            amine_smiles=req.amine_smiles.strip(),
            conditions=req.conditions, outcome=req.outcome,
            strength=req.strength.strip(), notes=req.notes.strip(),
            operator=req.operator.strip(),
            experiment_no=req.experiment_no.strip(),
            status=req.status.strip() or "final",
            process_notes=req.process_notes,
            timeline=req.timeline,
            self_summary=req.self_summary,
            mistakes=req.mistakes)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"记录保存失败：{type(exc).__name__}: {exc}")


@router.get("/{rec_id}")
def get_record(rec_id: str):
    rec = _store().get_record(rec_id)
    if not rec:
        raise HTTPException(404, f"记录 {rec_id} 不存在")
    return rec


@router.put("/{rec_id}")
def update_record(rec_id: str, req: RecordUpdate):
    """草稿继续编辑 / 转正式；正式记录也可更新流程文本与时间线条目。"""
    fields = req.model_dump(exclude_none=True)
    if not fields:
        rec = _store().get_record(rec_id)
        if not rec:
            raise HTTPException(404, f"记录 {rec_id} 不存在")
        return rec
    try:
        return _store().update_record(rec_id, fields)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"记录更新失败：{type(exc).__name__}: {exc}")


@router.delete("/{rec_id}")
def delete_record(rec_id: str):
    if not _store().delete_record(rec_id):
        raise HTTPException(404, f"记录 {rec_id} 不存在")
    return {"deleted": rec_id}


# ---------------------------------------------------------------------------
# Word 导出
# ---------------------------------------------------------------------------

_DOCX_MEDIA = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


@router.get("/{rec_id}/export")
def export_record_docx(rec_id: str):
    """导出该记录的完整 Word 报告（.docx 下载）。

    中文文件名走 RFC 5987 filename* 编码，同时给 ASCII 兜底名
    （与 /api/dft/jobs/{id}/export 同做法）；LLM 未配置 / 无打分快照
    时文档内对应小节降级为占位说明，不报错。
    """
    rec = _store().get_record(rec_id)
    if not rec:
        raise HTTPException(404, f"记录 {rec_id} 不存在")
    try:
        from records import export_docx
    except ImportError:  # pragma: no cover - src 直接在 sys.path 时
        from src.records import export_docx  # type: ignore
    data = export_docx.build_record_docx(rec, version=__version__)
    filename = export_docx.export_filename(rec)
    quoted = urllib.parse.quote(filename, encoding="utf-8")
    fallback = f"record_{rec_id}.docx"
    headers = {
        "Content-Disposition": (
            f"attachment; filename=\"{fallback}\"; "
            f"filename*=UTF-8''{quoted}"),
    }
    return Response(content=data, media_type=_DOCX_MEDIA, headers=headers)


@router.post("/export-bundle")
def export_records_bundle(req: RecordsBundleExport):
    """按收藏分组导出实验记录为一份 Word（.docx 下载）。

    封面含标题/导出时间/软件版本；按 favorite_ids 顺序分组，每组标题为
    单体组名称（醛+胺），组内按时间序列出该收藏关联的所有实验记录，
    内容与单条导出一致；favorite 无记录时标注「暂无实验记录」。
    favorite_ids 至少 1 个（空 → 400），任一收藏不存在 → 404。
    """
    favorite_ids = [str(fid).strip() for fid in req.favorite_ids if str(fid).strip()]
    if not favorite_ids:
        raise HTTPException(400, "favorite_ids 至少提供 1 个收藏 id")
    try:
        from favorites import store as fav_store
    except ImportError:  # pragma: no cover - src 直接在 sys.path 时
        from src.favorites import store as fav_store  # type: ignore
    try:
        from records import export_docx
    except ImportError:  # pragma: no cover - src 直接在 sys.path 时
        from src.records import export_docx  # type: ignore
    groups = []
    seen = set()
    for fid in favorite_ids:
        if fid in seen:  # 重复 id 去重，避免同一组出现两遍
            continue
        seen.add(fid)
        fav = fav_store.get_favorite(fid)
        if fav is None:
            raise HTTPException(404, f"收藏 {fid} 不存在")
        groups.append({
            "favorite": fav,
            "records": _store().list_records(favorite_id=fid),
        })
    data = export_docx.build_bundle_docx(groups, version=__version__)
    filename = export_docx.bundle_export_filename()
    quoted = urllib.parse.quote(filename, encoding="utf-8")
    headers = {
        "Content-Disposition": (
            f"attachment; filename=\"records_bundle.docx\"; "
            f"filename*=UTF-8''{quoted}"),
    }
    return Response(content=data, media_type=_DOCX_MEDIA, headers=headers)


# ---------------------------------------------------------------------------
# 时间线附件（上传 ≤20MB / 下载 / 删除）
# ---------------------------------------------------------------------------


@router.post("/{rec_id}/attachments", status_code=201)
async def upload_attachment(rec_id: str,
                            entry_id: str = Form(...),
                            file: UploadFile = File(...)):
    """给某时间点条目上传附件（multipart：entry_id + file）。"""
    data = await file.read()
    try:
        return _store().add_attachment(
            rec_id, entry_id, file.filename or "file", data)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"附件上传失败：{type(exc).__name__}: {exc}")


@router.get("/{rec_id}/attachments/{attachment_id}")
def download_attachment(rec_id: str, attachment_id: str):
    """下载/预览附件：图片与 pdf 内联，其余作为附件下载。"""
    found = _store().get_attachment_path(rec_id, attachment_id)
    if not found:
        raise HTTPException(404, f"附件 {attachment_id} 不存在")
    path, meta = found
    ext = str(meta.get("ext") or "").lower()
    mime = _MIME_BY_EXT.get(ext, "application/octet-stream")
    inline = bool(meta.get("is_image")) or ext == ".pdf"
    return FileResponse(
        path, media_type=mime, filename=str(meta.get("filename") or path.name),
        content_disposition_type="inline" if inline else "attachment")


@router.delete("/{rec_id}/attachments/{attachment_id}")
def delete_attachment(rec_id: str, attachment_id: str):
    if not _store().remove_attachment(rec_id, attachment_id):
        raise HTTPException(404, f"附件 {attachment_id} 不存在")
    return {"deleted": attachment_id}
