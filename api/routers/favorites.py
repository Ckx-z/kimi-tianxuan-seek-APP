"""收藏夹路由。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..schemas import FavoriteCreate, FavoriteUpdate

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
    # 交叉合并去重：同单体对（规范化 SMILES）已收藏 → 409 + 已存在摘要，
    # 前端据此弹「已收藏过该组合」提示，而非静默重复收藏
    existing = _store().find_favorite_by_pair(
        req.aldehyde_smiles.strip(), req.amine_smiles.strip())
    if existing:
        folder = _store().get_folder(str(existing.get("folder_id") or ""))
        snap = existing.get("latest_prediction")
        raise HTTPException(409, detail={
            "message": "已收藏过该单体组合，未重复创建",
            "existing": {
                "id": existing.get("id"),
                "folder_id": existing.get("folder_id"),
                "folder_name": (folder or {}).get("name", "收藏夹1"),
                "aldehyde_name": (existing.get("aldehyde") or {}).get("name", ""),
                "amine_name": (existing.get("amine") or {}).get("name", ""),
                "has_prediction": isinstance(snap, dict)
                and snap.get("score") is not None,
                "has_dft": isinstance(existing.get("dft_snapshot"), dict),
                "created_at": existing.get("created_at", ""),
            },
        })
    # 前端收藏时带上当前打分结果 → 一并落进 latest_prediction，
    # 「我的」页直接展示已存分数，不再误显「未打分」
    snapshot = None
    if req.score is not None:
        snapshot = {
            "score": req.score,
            "std": req.std,
            "ood": req.ood or "none",
            "score_policy": req.score_policy,
            "tree_score": req.tree_score,
            "gnn_score": req.gnn_score,
        }
    try:
        return _store().add_favorite(
            req.aldehyde_smiles.strip(), req.amine_smiles.strip(),
            ald_name=req.ald_name.strip(), amine_name=req.amine_name.strip(),
            notes=req.notes.strip(), prediction=snapshot,
            folder_id=req.folder_id, dft_snapshot=req.dft_snapshot)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"收藏保存失败：{type(exc).__name__}: {exc}")


@router.get("/{fav_id}")
def get_favorite(fav_id: str):
    fav = _store().get_favorite(fav_id)
    if not fav:
        raise HTTPException(404, f"收藏 {fav_id} 不存在")
    return fav


@router.patch("/{fav_id}")
def update_favorite(fav_id: str, req: FavoriteUpdate):
    """收藏局部更新：移夹（folder_id）/ 改备注 / 写入 DFT 快照。"""
    fields = req.model_dump(exclude_none=True)
    if not fields:
        fav = _store().get_favorite(fav_id)
        if not fav:
            raise HTTPException(404, f"收藏 {fav_id} 不存在")
        return fav
    try:
        return _store().update_favorite(fav_id, **fields)
    except KeyError:
        raise HTTPException(404, f"收藏 {fav_id} 不存在")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"收藏更新失败：{type(exc).__name__}: {exc}")


@router.delete("/{fav_id}")
def delete_favorite(fav_id: str):
    if not _store().delete_favorite(fav_id):
        raise HTTPException(404, f"收藏 {fav_id} 不存在")
    return {"deleted": fav_id}
