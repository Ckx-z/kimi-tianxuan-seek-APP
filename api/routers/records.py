"""实验记录路由。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..schemas import RecordCreate

router = APIRouter(prefix="/api/records", tags=["records"])


def _store():
    from records import store
    return store


@router.get("")
def list_records(favorite_id: str | None = None):
    return {"records": _store().list_records(favorite_id=favorite_id)}


@router.post("", status_code=201)
def create_record(req: RecordCreate):
    if not req.experiment_no.strip():
        raise HTTPException(400, "experiment_no（实验编号）为必填")
    try:
        return _store().create_record(
            favorite_id=req.favorite_id or None,
            aldehyde_smiles=req.aldehyde_smiles.strip(),
            amine_smiles=req.amine_smiles.strip(),
            conditions=req.conditions, outcome=req.outcome,
            strength=req.strength.strip(), notes=req.notes.strip(),
            operator=req.operator.strip(),
            experiment_no=req.experiment_no.strip())
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


@router.delete("/{rec_id}")
def delete_record(rec_id: str):
    if not _store().delete_record(rec_id):
        raise HTTPException(404, f"记录 {rec_id} 不存在")
    return {"deleted": rec_id}
