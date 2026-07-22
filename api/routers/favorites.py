"""收藏夹路由。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..schemas import FavoriteCreate

router = APIRouter(prefix="/api/favorites", tags=["favorites"])


def _store():
    from favorites import store
    return store


@router.get("")
def list_favorites():
    return {"favorites": _store().list_favorites()}


@router.post("", status_code=201)
def create_favorite(req: FavoriteCreate):
    if not req.aldehyde_smiles.strip() or not req.amine_smiles.strip():
        raise HTTPException(400, "醛/胺 SMILES 不能为空")
    try:
        return _store().add_favorite(
            req.aldehyde_smiles.strip(), req.amine_smiles.strip(),
            ald_name=req.ald_name.strip(), amine_name=req.amine_name.strip(),
            notes=req.notes.strip())
    except Exception as exc:
        raise HTTPException(500, f"收藏保存失败：{type(exc).__name__}: {exc}")


@router.get("/{fav_id}")
def get_favorite(fav_id: str):
    fav = _store().get_favorite(fav_id)
    if not fav:
        raise HTTPException(404, f"收藏 {fav_id} 不存在")
    return fav


@router.delete("/{fav_id}")
def delete_favorite(fav_id: str):
    if not _store().delete_favorite(fav_id):
        raise HTTPException(404, f"收藏 {fav_id} 不存在")
    return {"deleted": fav_id}
