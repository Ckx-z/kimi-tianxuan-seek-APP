"""收藏夹 Folder 路由（P2 收藏夹体系）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..schemas import FolderCreate, FolderRename

router = APIRouter(prefix="/api/favorite-folders", tags=["favorite-folders"])


def _store():
    from favorites import store
    return store


@router.get("")
def list_folders():
    """收藏夹列表（每项含 favorite_count）。"""
    return {"folders": _store().list_folders()}


@router.post("", status_code=201)
def create_folder(req: FolderCreate):
    try:
        return _store().create_folder(req.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.patch("/{folder_id}")
def rename_folder(folder_id: str, req: FolderRename):
    try:
        return _store().rename_folder(folder_id, req.name)
    except KeyError:
        raise HTTPException(404, f"收藏夹 {folder_id} 不存在")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.delete("/{folder_id}")
def delete_folder(folder_id: str):
    """删夹连带删内收藏，响应返回删除的收藏条数；最后一个夹禁止删除。"""
    try:
        deleted = _store().delete_folder(folder_id)
    except KeyError:
        raise HTTPException(404, f"收藏夹 {folder_id} 不存在")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"deleted": folder_id, "deleted_favorites": deleted}
