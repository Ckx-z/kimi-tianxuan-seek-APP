"""实验记录路由（含草稿暂存、时间线附件上传/下载/删除）。"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..schemas import RecordCreate, RecordUpdate

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
            timeline=req.timeline)
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
