"""收藏相关工具：list_favorites（读）与 manage_favorite（写，需二次确认）。

内部直调 src/favorites/store（不走 HTTP 自调用）。manage_favorite 幂等：
- add：同单体对已收藏 → 不重复建条目，按需移夹并如实说明；
- move：已在目标夹 → 如实说明，不产生变更；
- delete：条目不存在 → 如实说明（视为已删除），不报错。
"""

from __future__ import annotations

try:
    from src.favorites import store as fav_store
except ImportError:  # pragma: no cover
    from favorites import store as fav_store  # type: ignore

_MAX_LIST = 30   # 单次最多列出的收藏条数（token 成本控制）


def _fmt_score(snap) -> str:
    if not isinstance(snap, dict) or snap.get("score") is None:
        return "未打分"
    score = snap.get("score")
    ood = snap.get("ood") or "none"
    s = f"{float(score):.3f}" if isinstance(score, (int, float)) else str(score)
    return f"{s}（OOD={ood}）" if ood != "none" else s


def _monomer_label(m: dict) -> str:
    m = m or {}
    return m.get("name") or m.get("cas") or m.get("smiles") or "?"


def _fmt_favorite(fav: dict, folder_names: dict[str, str]) -> str:
    fid = fav.get("id")
    folder = folder_names.get(str(fav.get("folder_id") or ""), "（未分夹）")
    dft = "有" if isinstance(fav.get("dft_snapshot"), dict) else "无"
    lines = [
        f"- {fid}｜醛 {_monomer_label(fav.get('aldehyde'))}"
        f" / 胺 {_monomer_label(fav.get('amine'))}",
        f"  收藏夹：{folder}｜最新打分：{_fmt_score(fav.get('latest_prediction'))}"
        f"｜DFT 快照：{dft}｜实验记录 {len(fav.get('experiment_record_ids') or [])} 条",
    ]
    if fav.get("notes"):
        lines.append(f"  备注：{str(fav['notes'])[:80]}")
    return "\n".join(lines)


def list_favorites_tool(folder_id: str | None = None, limit: int = 20) -> dict:
    """列收藏夹与收藏条目。folder_id 可选（只看某夹）；limit 控条数。"""
    try:
        folders = fav_store.list_folders()
        fid_filter = (folder_id or "").strip() or None
        if fid_filter and fav_store.get_folder(fid_filter) is None:
            return {"text": f"收藏夹不存在：{fid_filter}（可用 list_favorites "
                            "不传参数先看全部收藏夹）",
                    "details": {"folders": folders}, "is_error": True}
        favs = fav_store.list_favorites()
        if fid_filter:
            favs = [f for f in favs
                    if str(f.get("folder_id") or "") == fid_filter]
    except Exception as exc:
        return {"text": f"收藏读取失败：{type(exc).__name__}: {exc}",
                "details": {}, "is_error": True}

    folder_names = {str(f.get("id")): str(f.get("name")) for f in folders}
    header_lines = ["## 收藏夹概览"]
    if folders:
        for f in folders:
            header_lines.append(
                f"- {f.get('name')}（id: {f.get('id')}，{f.get('favorite_count', 0)} 条）")
    else:
        header_lines.append("- （尚无收藏夹）")

    if not favs:
        scope = f"收藏夹 {folder_names.get(fid_filter, fid_filter)} 内" \
            if fid_filter else "系统内"
        header_lines.append(f"\n{scope}暂无收藏条目。")
        return {"text": "\n".join(header_lines),
                "details": {"count": 0, "folders": folders}, "is_error": False}

    limit = max(1, min(int(limit or 20), _MAX_LIST))
    shown = favs[:limit]
    header_lines.append(
        f"\n## 收藏条目（共 {len(favs)} 条"
        + (f"，收藏夹过滤：{folder_names.get(fid_filter, fid_filter)}" if fid_filter else "")
        + f"，示最近 {len(shown)} 条）")
    body = "\n".join(_fmt_favorite(f, folder_names) for f in shown)
    return {
        "text": "\n".join(header_lines) + "\n" + body,
        "details": {
            "count": len(favs),
            "shown": len(shown),
            "folder_id": fid_filter,
            "favorite_ids": [f.get("id") for f in shown],
            "folders": [{"id": f.get("id"), "name": f.get("name")}
                        for f in folders],
        },
        "is_error": False,
    }


# ---------------------------------------------------------------- 写操作

def _resolve_folder(folder_id: str = "", folder_name: str = "") -> dict:
    """按 id / 名称解析目标收藏夹；名称未命中时新建。均缺省 → 兜底夹。"""
    folder_id = (folder_id or "").strip()
    folder_name = (folder_name or "").strip()
    if folder_id:
        folder = fav_store.get_folder(folder_id)
        if folder is None:
            raise ValueError(f"收藏夹不存在：{folder_id}")
        return folder
    if folder_name:
        for f in fav_store.list_folders():
            if str(f.get("name")) == folder_name:
                return fav_store.get_folder(str(f.get("id"))) or f
        return fav_store.create_folder(folder_name)
    return fav_store._ensure_default_folder()


def _current_snapshot(ald_smiles: str, amine_smiles: str) -> dict | None:
    """取当前打分快照：先查预测日志，查不到再现场打分（失败静默返回 None）。"""
    snap = fav_store._snapshot_from_log(ald_smiles, amine_smiles)
    if snap is not None:
        return snap
    try:
        from api.deps import build_prediction_payload, get_predictor
        pred = get_predictor()
        result = pred.predict(ald_smiles, amine_smiles)
        payload = build_prediction_payload(ald_smiles, amine_smiles, result,
                                           source="assistant")
        if payload.get("score") is None:
            return None
        return {
            "score": payload.get("score"),
            "std": payload.get("tree_std"),
            "arm": payload.get("tree_route") or "",
            "ood": (payload.get("ood") or {}).get("level", "none"),
            "score_policy": payload.get("score_policy"),
            "tree_score": payload.get("tree_score"),
            "gnn_score": payload.get("gnn_score"),
        }
    except Exception:
        return None


def _add_favorite(args: dict) -> dict:
    ald = (args.get("ald_smiles") or "").strip()
    amine = (args.get("amine_smiles") or "").strip()
    if not ald or not amine:
        return {"text": "参数缺失：收藏操作需要 ald_smiles 与 amine_smiles",
                "details": {}, "is_error": True}
    folder = _resolve_folder(args.get("folder_id") or "",
                             args.get("folder_name") or "")
    notes = (args.get("notes") or "").strip()

    # 幂等：同单体对已收藏 → 不重复建条目，按需移夹
    existing = fav_store.find_favorite_by_pair(ald, amine)
    if existing is not None:
        fid = str(existing.get("id"))
        if str(existing.get("folder_id") or "") != folder["id"]:
            fav_store.update_favorite(fid, folder_id=folder["id"])
            return {"text": f"该单体组已在收藏中（{fid}），已移至"
                            f"「{folder['name']}」，未重复收藏。",
                    "details": {"favorite_id": fid, "folder_id": folder["id"],
                                "deduplicated": True},
                    "is_error": False}
        return {"text": f"该单体组已在「{folder['name']}」中（{fid}），"
                        "未重复收藏。",
                "details": {"favorite_id": fid, "folder_id": folder["id"],
                            "deduplicated": True},
                "is_error": False}

    snapshot = _current_snapshot(ald, amine)
    fav = fav_store.add_favorite(
        ald, amine,
        ald_name=(args.get("ald_name") or "").strip(),
        amine_name=(args.get("amine_name") or "").strip(),
        notes=notes, prediction=snapshot, folder_id=folder["id"])
    snap_note = (f"，已附当前打分快照（分数 "
                 f"{float(snapshot['score']):.3f}）"
                 if snapshot and snapshot.get("score") is not None
                 else "，暂无打分快照（可稍后打分自动回填）")
    return {"text": f"已收藏到「{folder['name']}」（条目 {fav['id']}）{snap_note}。",
            "details": {"favorite_id": fav["id"], "folder_id": folder["id"],
                        "folder_name": folder["name"],
                        "has_snapshot": snapshot is not None},
            "is_error": False}


def _move_favorite(args: dict) -> dict:
    fid = (args.get("favorite_id") or "").strip()
    if not fid:
        return {"text": "参数缺失：移动收藏需要 favorite_id",
                "details": {}, "is_error": True}
    folder = _resolve_folder(args.get("folder_id") or "",
                             args.get("folder_name") or "")
    fav = fav_store.get_favorite(fid)
    if fav is None:
        return {"text": f"收藏条目不存在：{fid}", "details": {}, "is_error": True}
    if str(fav.get("folder_id") or "") == folder["id"]:
        return {"text": f"{fid} 已在「{folder['name']}」中，无需移动。",
                "details": {"favorite_id": fid, "folder_id": folder["id"]},
                "is_error": False}
    fav_store.update_favorite(fid, folder_id=folder["id"])
    return {"text": f"已把 {fid} 移至「{folder['name']}」。",
            "details": {"favorite_id": fid, "folder_id": folder["id"],
                        "folder_name": folder["name"]},
            "is_error": False}


def _delete_favorite(args: dict) -> dict:
    fid = (args.get("favorite_id") or "").strip()
    if not fid:
        return {"text": "参数缺失：删除收藏需要 favorite_id",
                "details": {}, "is_error": True}
    if fav_store.delete_favorite(fid):
        return {"text": f"已删除收藏 {fid}。",
                "details": {"favorite_id": fid, "deleted": True},
                "is_error": False}
    return {"text": f"收藏 {fid} 不存在或已删除，无需重复操作。",
            "details": {"favorite_id": fid, "deleted": False},
            "is_error": False}


def manage_favorite(args: dict) -> dict:
    """收藏管理写操作：add（收藏到指定夹，含打分快照）/ move（移夹）/ delete。"""
    args = args if isinstance(args, dict) else {}
    action = (args.get("action") or "").strip().lower()
    try:
        if action == "add":
            return _add_favorite(args)
        if action == "move":
            return _move_favorite(args)
        if action == "delete":
            return _delete_favorite(args)
        return {"text": f"未知 action：{action or '（空）'}（可选 add / move / delete）",
                "details": {}, "is_error": True}
    except (ValueError, KeyError) as exc:
        return {"text": str(exc), "details": {}, "is_error": True}
    except Exception as exc:
        return {"text": f"收藏操作失败：{type(exc).__name__}: {exc}",
                "details": {}, "is_error": True}


def manage_favorite_impact(args: dict) -> str:
    """二次确认卡上的影响说明。"""
    args = args if isinstance(args, dict) else {}
    action = (args.get("action") or "").strip().lower()
    folder = args.get("folder_name") or args.get("folder_id") or "默认收藏夹"
    if action == "delete":
        return (f"将删除收藏条目 {args.get('favorite_id') or '（未指定）'}，"
                "删除后不可恢复（关联的实验记录不受影响）。")
    if action == "move":
        return (f"将把收藏 {args.get('favorite_id') or '（未指定）'} "
                f"移动到「{folder}」。")
    return (f"将把该醛/胺组合收藏到「{folder}」，并尝试附带当前打分快照；"
            "同组合已收藏时不会重复添加。")
