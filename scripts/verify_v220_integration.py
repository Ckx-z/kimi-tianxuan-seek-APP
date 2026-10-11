#!/usr/bin/env python
"""v2.20 四功能独立验收脚本（真实 HTTP 调用 + 落盘文件双重校验）。

用法（任选本机可用解释器，脚本自身不依赖任何绝对路径）：
    python scripts/verify_v220_integration.py          # 跑完自动清理临时数据目录
    python scripts/verify_v220_integration.py --keep   # 保留临时数据目录便于排查

覆盖（v2.20）：
  1. 实验记录「实验进展」progress：创建 / 筛选 / 非法值 400 / 编辑 / 与 status 并存 / 落盘；
  2. 收藏档案卡：archive_type 派生（cof_monomer_group | dft_pair）+ 单体名称/CAS 可编辑；
  3. DFT 计算记录：同条件指纹更新、不同条件新增 + A/B/C 标号、失败记录可删且持久化；
  4. 科研日记：CRUD + mentions 关联 + by-record 反查 + 助手工具 read_diary（含空结果不编造）。

隔离纪律（本脚本不会写任何真实用户数据）：
  - **先**把 COF_DATA_DIR 指到自建临时目录，**再**导入仓库模块，于是
    runtime_config.user_data_root() == <临时目录>/data；导入后再显式校验/钉住
    records/favorites 的导入期路径常量；
  - 运行前后对仓库 data/ 下相关目录做文件快照比对，一旦有新增/改动即判失败；
  - 退出码：0 = 全部通过，1 = 有失败项，2 = 脚本自身异常（未跑完）。

本脚本不使用（也不复用）仓库既有测试的任何断言或结论，全部为独立编写。
"""

from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import sys
import tempfile
import traceback
from datetime import date
from pathlib import Path

REPO_ROOT = Path(os.environ.get("COF_VERIFY_REPO_ROOT")
                 or Path(__file__).resolve().parents[1]).resolve()

#: 本次运行使用的临时数据根（bootstrap() 里赋值，退出前清理）
TMP_ROOT: Path | None = None

# 被管道捕获时强制 UTF-8，保证中文输出不乱码；终端直连时保持系统编码
if not sys.stdout.isatty():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

# --------------------------------------------------------------------------- 输出
#: 检查分两类：feature = v2.20 四项功能的验收点；consistency = 附加的数据一致性红线
_FAILURES: list[tuple[str, str]] = []
_COUNTS = {"feature": 0, "consistency": 0}
_CHECKS = 0


def check(ok, desc: str, detail: str = "", kind: str = "feature") -> bool:
    """记录并打印一条检查结果；返回布尔值便于串联断言。

    kind="feature"：四功能验收点（决定「结果 = ALL PASS」）；
    kind="consistency"：附加一致性红线（例如磁盘上不许出现与条目矛盾的过期
    派生字段）——同样计入失败并以非零退出码结束，但汇总时单独归类，避免与
    「功能是否可用」混为一谈。
    """
    global _CHECKS
    _CHECKS += 1
    _COUNTS[kind] = _COUNTS.get(kind, 0) + 1
    if ok:
        print(f"[OK] {desc}")
    else:
        print(f"[FAIL] {desc}")
        if detail:
            print(f"       - {detail}")
        _FAILURES.append((kind, desc))
    return bool(ok)


def info(msg: str) -> None:
    print(f"[INFO] {msg}")


def note(msg: str) -> None:
    print(f"[NOTE] {msg}")


def section(title: str) -> None:
    print(f"\n===== {title} =====")


# --------------------------------------------------------------------------- 工具


def _snippet(resp, limit: int = 400) -> str:
    """响应片段（优先 JSON，失败回退文本），用于失败时贴证据。"""
    try:
        text = json.dumps(resp.json(), ensure_ascii=False)
    except Exception:
        text = str(getattr(resp, "text", ""))
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def _detail(resp) -> str:
    return (f"HTTP {getattr(resp, 'status_code', '?')} 响应片段: {_snippet(resp)}")


def path_inside(path, parent) -> bool:
    try:
        p = Path(path).resolve()
        q = Path(parent).resolve()
    except Exception:
        return False
    return p == q or q in p.parents


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def snapshot_dirs(dirs) -> dict:
    """目录树文件快照 {绝对路径: mtime_ns}（用于确认真实数据目录未被写入）。"""
    snap = {}
    for d in dirs:
        d = Path(d)
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.is_file():
                try:
                    snap[str(p.resolve())] = p.stat().st_mtime_ns
                except OSError:
                    pass
    return snap


# --------------------------------------------------------------------------- 引导


def bootstrap() -> Path:
    """建临时数据根 + 设 COF_DATA_DIR（必须在导入仓库模块之前调用）。"""
    global TMP_ROOT
    tmp_root = Path(tempfile.mkdtemp(prefix="cof_v220_verify_"))
    TMP_ROOT = tmp_root
    os.environ["COF_DATA_DIR"] = str(tmp_root)
    os.environ.setdefault("COF_CREST_DOCKER_AUTOSTART", "0")
    for p in (REPO_ROOT, REPO_ROOT / "src"):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return tmp_root


#: (模块名, 常量名, 相对 user_data_root 的子路径) —— 导入期固化的写盘常量
PATH_CONSTS = (
    ("records.store", "RECORDS_DIR", "rag_export/records"),
    ("records.store", "ATTACHMENTS_DIR", "experiment_records/attachments"),
    ("favorites.store", "FAVORITES_DIR", "favorites"),
    ("src.records.store", "RECORDS_DIR", "rag_export/records"),
    ("src.records.store", "ATTACHMENTS_DIR", "experiment_records/attachments"),
    ("src.favorites.store", "FAVORITES_DIR", "favorites"),
)


def pin_store_paths(user_data_root: Path) -> tuple[list[str], list[str]]:
    """校验并把 records/favorites 的导入期路径常量钉到隔离目录。

    返回 (钉之前就已经正确的描述, 被运行时重定向的描述)。重定向不是失败
    （既有测试也用同样做法），但必须显式报告——它意味着常量在导入时就已
    指向别处（那才是污染风险）。
    """
    already, repinned = [], []
    for mod_name, attr, rel in PATH_CONSTS:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        want = user_data_root / rel
        cur = getattr(mod, attr, None)
        label = f"{mod_name}.{attr}"
        if cur is not None and Path(cur) == want:
            already.append(label)
        else:
            setattr(mod, attr, want)
            repinned.append(f"{label}: {cur} -> {want}")
    return already, repinned


# --------------------------------------------------------------------------- 步骤 1


def step1_records(client, rec_store) -> dict:
    """功能 1：实验记录「实验进展」progress。"""
    section("功能 1/4：实验记录「实验进展」progress（三选一互斥 / 筛选 / 编辑 / 落盘）")

    check(tuple(getattr(rec_store, "PROGRESS_VALUES", ())) ==
          ("running", "finished", "xrd_done"),
          "后端 progress 取值契约 == (running, finished, xrd_done)",
          f"实际: {getattr(rec_store, 'PROGRESS_VALUES', None)!r}")
    labels = getattr(rec_store, "PROGRESS_LABELS", {})
    check(labels.get("running") == "实验进行中" and labels.get("finished") == "实验结束"
          and labels.get("xrd_done") == "XRD 已完成",
          "三个取值各有且仅有唯一中文标签（互斥三选一的展示口径）",
          f"实际: {labels!r}")

    tp, pa = "O=CC1=C(C=O)C(=O)C(C=O)=C1O", "Nc1ccc(N)cc1"

    # --- 创建：progress=xrd_done 的正式记录
    r = client.post("/api/records", json={
        "aldehyde_smiles": tp, "amine_smiles": pa,
        "experiment_no": "V220-VERIFY-XRD", "outcome": "film",
        "progress": "xrd_done", "notes": "独立验收：XRD 已完成",
    })
    check(r.status_code == 201, "POST /api/records（progress=xrd_done）返回 201",
          _detail(r))
    if r.status_code != 201:
        return {}
    rec = r.json()
    rec_id = rec.get("record_id", "")
    check(bool(re.match(r"^rec_\d{8}_\d{3}$", str(rec_id))),
          f"返回的 record_id 形如 rec_YYYYMMDD_NNN（{rec_id}）")
    check(rec.get("progress") == "xrd_done", "创建响应里 progress == xrd_done",
          f"实际: {rec.get('progress')!r}")
    check(rec.get("status") == "final",
          "progress 与既有 status 并存：同一响应里 status == final",
          f"实际 status={rec.get('status')!r}")
    rec_path = Path(rec_store.RECORDS_DIR) / f"{rec_id}.json"
    on_disk = read_json(rec_path) if rec_path.is_file() else {}
    check(rec_path.is_file() and on_disk.get("progress") == "xrd_done",
          "落盘文件 rec_*.json 里确实写入了 progress=xrd_done",
          f"文件: {rec_path} 存在={rec_path.is_file()} 内容 progress={on_disk.get('progress')!r}")

    # --- 再建一条 running，作为筛选的对照组
    r2 = client.post("/api/records", json={
        "aldehyde_smiles": tp, "amine_smiles": pa,
        "experiment_no": "V220-VERIFY-RUN", "outcome": "partial",
        "progress": "running", "notes": "独立验收：进行中",
    })
    rec2_id = r2.json().get("record_id") if r2.status_code == 201 else ""
    check(r2.status_code == 201 and rec2_id != rec_id,
          "POST /api/records（progress=running）返回 201 且编号不同",
          _detail(r2))

    # --- 筛选
    r = client.get("/api/records", params={"progress": "xrd_done"})
    ids = [x.get("record_id") for x in r.json().get("records", [])] \
        if r.status_code == 200 else []
    check(r.status_code == 200 and rec_id in ids and rec2_id not in ids,
          "GET /api/records?progress=xrd_done 只筛到 xrd_done 那条",
          f"HTTP {r.status_code}，筛到 {ids}")
    r = client.get("/api/records")
    ids_all = [x.get("record_id") for x in r.json().get("records", [])] \
        if r.status_code == 200 else []
    check(r.status_code == 200 and rec_id in ids_all and rec2_id in ids_all,
          "GET /api/records（不带 progress）返回全部（不筛选）",
          f"HTTP {r.status_code}，返回 {ids_all}")

    # --- 非法值 400
    r = client.get("/api/records", params={"progress": "bogus"})
    detail = str(r.json().get("detail", "")) if r.status_code == 400 else _snippet(r)
    check(r.status_code == 400, "GET /api/records?progress=bogus 返回 400（不静默忽略）",
          _detail(r))
    check(r.status_code == 400 and "progress" in detail and "xrd_done" in detail,
          "400 的 detail 说明了 progress 的合法取值",
          f"detail={detail!r}")
    r = client.get("/api/records", params={"progress": "RUNNING"})
    check(r.status_code == 400,
          "progress 取值严格小写：大写 RUNNING 也算非法 → 400",
          _detail(r))
    r = client.post("/api/records", json={
        "aldehyde_smiles": tp, "amine_smiles": pa,
        "experiment_no": "V220-VERIFY-BAD", "outcome": "film",
        "progress": "done",
    })
    check(r.status_code == 400,
          "创建时 progress=done（非法）同样 400（写路径也校验，不只是筛选）",
          _detail(r))

    # --- 编辑：xrd_done -> finished
    r = client.put(f"/api/records/{rec_id}", json={"progress": "finished"})
    check(r.status_code == 200 and r.json().get("progress") == "finished",
          "PUT /api/records/{id} progress=finished 返回 200 且响应已更新",
          _detail(r))
    on_disk = read_json(rec_path) if rec_path.is_file() else {}
    check(on_disk.get("progress") == "finished",
          "编辑后落盘文件里的 progress 已变为 finished",
          f"磁盘 progress={on_disk.get('progress')!r}")
    r = client.get(f"/api/records/{rec_id}")
    check(r.status_code == 200 and r.json().get("progress") == "finished",
          "重新 GET /api/records/{id} 读回 finished（持久化，非内存假象）",
          _detail(r))

    # --- 编辑后筛选结果随之变化
    ids_xrd = [x.get("record_id") for x in
               client.get("/api/records", params={"progress": "xrd_done"}).json()
               .get("records", [])]
    ids_fin = [x.get("record_id") for x in
               client.get("/api/records", params={"progress": "finished"}).json()
               .get("records", [])]
    check(rec_id not in ids_xrd and rec_id in ids_fin,
          "改成 finished 后：xrd_done 筛不到它、finished 能筛到它（筛选随编辑变化）",
          f"xrd_done={ids_xrd} finished={ids_fin}")

    # --- 非法 progress 编辑必须 400 且不改动落盘
    r = client.put(f"/api/records/{rec_id}", json={"progress": "done"})
    detail = str(r.json().get("detail", "")) if r.status_code == 400 else _snippet(r)
    check(r.status_code == 400 and "progress" in detail,
          "PUT progress=done（非法）返回 400",
          _detail(r))
    on_disk = read_json(rec_path) if rec_path.is_file() else {}
    check(on_disk.get("progress") == "finished",
          "非法编辑后落盘 progress 未被破坏（仍是 finished）",
          f"磁盘 progress={on_disk.get('progress')!r}")

    # --- draft 与 progress 并存（两个维度互不影响）
    r = client.post("/api/records", json={
        "aldehyde_smiles": tp, "amine_smiles": pa,
        "status": "draft", "outcome": "", "progress": "finished",
        "notes": "独立验收：草稿 + finished",
    })
    draft = r.json() if r.status_code == 201 else {}
    check(r.status_code == 201 and draft.get("status") == "draft"
          and draft.get("progress") == "finished",
          "draft 记录也能带 progress（status=draft 与 progress=finished 并存）",
          _detail(r))
    if draft.get("record_id"):
        dpath = Path(rec_store.RECORDS_DIR) / f"{draft['record_id']}.json"
        dd = read_json(dpath) if dpath.is_file() else {}
        check(dd.get("status") == "draft" and dd.get("progress") == "finished",
              "草稿的 status/progress 两个字段同时落盘",
              f"磁盘 status={dd.get('status')!r} progress={dd.get('progress')!r}")

    # --- 旧记录（无 progress 字段）读取兼容
    legacy_id = "rec_20250101_777"
    legacy = {
        "schema_version": "1.0", "record_type": "experiment_record",
        "record_id": legacy_id, "experiment_no": "LEGACY-1", "status": "final",
        "favorite_id": None, "aldehyde": {"smiles": tp, "cas": "", "name": ""},
        "amine": {"smiles": pa, "cas": "", "name": ""},
        "conditions": {}, "outcome": "film", "notes": "老数据（无 progress 字段）",
        "date": "2025-01-01",
    }
    lpath = Path(rec_store.RECORDS_DIR) / f"{legacy_id}.json"
    lpath.parent.mkdir(parents=True, exist_ok=True)
    lpath.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    r = client.get(f"/api/records/{legacy_id}")
    check(r.status_code == 200 and r.json().get("progress") == "running",
          "旧记录（json 里没有 progress）读出来补默认 running",
          _detail(r))
    ids_run = [x.get("record_id") for x in
               client.get("/api/records", params={"progress": "running"}).json()
               .get("records", [])]
    check(legacy_id in ids_run,
          "旧记录能被 progress=running 筛选到（读取兼容口径一致）",
          f"running={ids_run}")

    return {"rec_id": rec_id, "rec2_id": rec2_id}


# --------------------------------------------------------------------------- 步骤 2


def step2_archive_card(client, fav_store) -> dict:
    """功能 2：收藏档案卡（archive_type 派生 + 名称/CAS 可编辑）。"""
    section("功能 2/4：收藏档案卡（archive_type 派生 + 单体名称/CAS 可编辑）")

    tp, pa = "O=CC1=C(C=O)C(=O)C(C=O)=C1O", "Nc1ccc(N)cc1"
    r = client.post("/api/favorites", json={
        "aldehyde_smiles": tp, "amine_smiles": pa,
        "ald_name": "验收醛", "amine_name": "验收胺", "notes": "v2.20 独立验收",
    })
    check(r.status_code == 201, "POST /api/favorites 返回 201", _detail(r))
    if r.status_code != 201:
        return {}
    fav = r.json()
    fav_id = fav.get("id", "")
    check(bool(re.match(r"^fav_\d{8}_\d{3}$", str(fav_id))),
          f"返回的收藏 id 形如 fav_YYYYMMDD_NNN（{fav_id}）")
    check(fav.get("archive_type") == "cof_monomer_group",
          "无 DFT 记录的收藏 → archive_type == cof_monomer_group（COF 单体组卡）",
          f"实际: {fav.get('archive_type')!r}")
    fav_path = Path(fav_store.FAVORITES_DIR) / f"{fav_id}.json"
    check(fav_path.is_file(), "收藏已落盘（data/favorites/fav_*.json）",
          f"文件不存在: {fav_path}")
    listed = [f.get("id") for f in
              client.get("/api/favorites").json().get("favorites", [])] \
        if client.get("/api/favorites").status_code == 200 else []
    check(fav_id in listed, "GET /api/favorites 列表里能查到新建的收藏",
          f"列表: {listed}")

    # --- PATCH monomer-meta：改名称 + 手动补 CAS
    old_smiles = (fav.get("aldehyde", {}).get("smiles"),
                  fav.get("amine", {}).get("smiles"))
    r = client.patch(f"/api/favorites/{fav_id}/monomer-meta", json={
        "aldehyde": {"name": "验收醛-改名", "cas": "123-45-6"},
        "amine": {"cas": "77-78-1"},
    })
    check(r.status_code == 200, "PATCH /api/favorites/{id}/monomer-meta 返回 200",
          _detail(r))
    patched = r.json() if r.status_code == 200 else {}
    ald, am = patched.get("aldehyde", {}), patched.get("amine", {})
    check(ald.get("name") == "验收醛-改名" and ald.get("cas") == "123-45-6",
          "响应里醛的名称与手动补充的 CAS 都已生效",
          f"实际 aldehyde={ald!r}")
    check(am.get("cas") == "77-78-1" and am.get("name") == "验收胺",
          "只传 CAS 时胺的名称不受影响（缺省字段=不改）",
          f"实际 amine={am!r}")
    check((ald.get("smiles"), am.get("smiles")) == old_smiles,
          "PATCH monomer-meta 不动 SMILES（只允许改名称/CAS）",
          f"之前={old_smiles} 之后={(ald.get('smiles'), am.get('smiles'))}")
    r = client.get(f"/api/favorites/{fav_id}")
    g = r.json() if r.status_code == 200 else {}
    check(g.get("aldehyde", {}).get("name") == "验收醛-改名"
          and g.get("amine", {}).get("cas") == "77-78-1",
          "重新 GET 收藏，名称/CAS 修改已持久化",
          _detail(r))
    disk = read_json(fav_path) if fav_path.is_file() else {}
    check(disk.get("aldehyde", {}).get("name") == "验收醛-改名"
          and disk.get("aldehyde", {}).get("cas") == "123-45-6"
          and disk.get("amine", {}).get("cas") == "77-78-1",
          "落盘 fav_*.json 里能看到改名与 CAS",
          f"磁盘 aldehyde={disk.get('aldehyde')!r} amine={disk.get('amine')!r}")
    r = client.patch(f"/api/favorites/{fav_id}/monomer-meta",
                     json={"aldehyde": {}})
    check(r.status_code == 400, "PATCH monomer-meta 传空 patch（无任何修改项）→ 400",
          _detail(r))
    r = client.patch(f"/api/favorites/fav_19700101_001/monomer-meta",
                     json={"aldehyde": {"name": "x"}})
    check(r.status_code == 404, "PATCH 不存在的收藏 → 404", _detail(r))
    # 全角连字符的 CAS 归一（store 文档口径：－/—/– 统一成半角 -）
    r = client.patch(f"/api/favorites/{fav_id}/monomer-meta",
                     json={"amine": {"cas": "1234－56－7"}})
    got = (r.json().get("amine", {}) or {}).get("cas") if r.status_code == 200 else None
    check(r.status_code == 200 and got == "1234-56-7",
          "手动补 CAS 时全角连字符被归一成半角（1234－56－7 → 1234-56-7）",
          f"HTTP {r.status_code} cas={got!r}")
    r = client.patch(f"/api/favorites/{fav_id}/monomer-meta",
                     json={"amine": {"cas": "77-78-1"}})
    check(r.status_code == 200, "（还原）CAS 改回 77-78-1", _detail(r))

    # --- 双分子 DFT 特殊卡：全部条目为 pair 模式 → dft_pair
    pa2 = "Nc1ccc(-c2ccc(N)cc2)cc1"
    r = client.post("/api/favorites", json={
        "aldehyde_smiles": tp, "amine_smiles": pa2,
        "ald_name": "验收醛2", "amine_name": "验收胺2",
    })
    fav2 = r.json() if r.status_code == 201 else {}
    fav2_id = fav2.get("id", "")
    check(r.status_code == 201 and bool(fav2_id),
          "新建第二个收藏（不同胺，用于 pair 模式档案卡）", _detail(r))
    if fav2_id:
        r = client.post(f"/api/favorites/{fav2_id}/dft-entries", json={
            "job_id": "dft_verify_pair_1", "mode": "pair", "method": "GFN2-xTB",
            "backend": "xtb", "x_type": "custom",
            "custom_smiles": "c1ccccc1", "a_smiles": tp, "b_smiles": pa2,
            "e_bind_kcal": -8.8,
        })
        pair_fav = r.json() if r.status_code == 200 else {}
        check(r.status_code == 200
              and pair_fav.get("archive_type") == "dft_pair",
              "DFT 记录全部来自 pair 模式 → archive_type == dft_pair（双分子 DFT 特殊卡）",
              f"HTTP {r.status_code} archive_type={pair_fav.get('archive_type')!r}")
        # 用户口径（v2.20 定夺）：只要含任一 pair 记录 → 判为双分子 DFT 特殊卡
        r = client.post(f"/api/favorites/{fav2_id}/dft-entries", json={
            "job_id": "dft_verify_pair_2", "mode": "dimer", "method": "GFN2-xTB",
            "backend": "xtb", "x_type": "self_stack",
        })
        mixed = r.json() if r.status_code == 200 else {}
        note("pair 卡混入一条 dimer 记录后 archive_type="
             f"{mixed.get('archive_type')!r}（v2.20 用户口径：含任一 pair 记录即为 "
             "dft_pair，见 src/favorites/store.py::_archive_type_of）")
        fav2_path = Path(fav_store.FAVORITES_DIR) / f"{fav2_id}.json"
        d2 = read_json(fav2_path) if fav2_path.is_file() else {}
        check(mixed.get("archive_type") == "dft_pair"
              and "archive_type" not in d2,
              "混入 dimer 后仍判 dft_pair，且磁盘不残留派生副本",
              f"响应={mixed.get('archive_type')!r} 磁盘键={sorted(d2)}",
              kind="consistency")

    return {"fav_id": fav_id, "fav_path": fav_path, "fav2_id": fav2_id}


# --------------------------------------------------------------------------- 步骤 3


def step3_dft_entries(client, fav_store, fav_id: str, fav_path: Path) -> dict:
    """功能 3：DFT 计算记录规范（指纹去重 / 新增标号 / 可删）。"""
    section("功能 3/4：DFT 计算记录（同条件指纹更新 / 不同条件新增 / A-B-C 标号 / 失败可删）")

    base = {
        "job_id": "dft_verify_job_1", "mode": "dimer", "method": "GFN2-xTB",
        "backend": "xtb", "x_type": "self_stack", "x_smiles": "c1ccccc1",
        "n_samples": 1, "with_props": False,
    }

    r = client.post(f"/api/favorites/{fav_id}/dft-entries",
                    json={**base, "e_bind_kcal": -12.3})
    d1 = r.json() if r.status_code == 200 else {}
    e1 = (d1.get("dft_entries") or [{}])[0] if d1.get("dft_entries") else {}
    check(r.status_code == 200 and len(d1.get("dft_entries") or []) == 1,
          "第 1 次提交 DFT 条目 → dft_entries 长度 1",
          _detail(r))
    check(bool(e1.get("condition_key")) and e1.get("label") == "A"
          and e1.get("mode") == "dimer",
          "条目带 condition_key / label=A / mode=dimer",
          f"实际条目: {json.dumps(e1, ensure_ascii=False)[:300]}")

    # --- 同条件再提交一次（只改结果）→ 不应新增
    r = client.post(f"/api/favorites/{fav_id}/dft-entries",
                    json={**base, "e_bind_kcal": -13.9})
    d2 = r.json() if r.status_code == 200 else {}
    ents = d2.get("dft_entries") or []
    check(r.status_code == 200 and len(ents) == 1,
          "同一条件指纹再次提交 → 仍然只有 1 条（同条件指纹更新，不堆重复记录）",
          f"HTTP {r.status_code} 实际长度 {len(ents)}")
    check(len(ents) == 1 and ents[0].get("label") == "A"
          and ents[0].get("e_bind_kcal") == -13.9,
          "同条件更新：标号保持 A、结果被本次覆盖为 -13.9",
          f"实际: {json.dumps(ents[0] if ents else {}, ensure_ascii=False)[:300]}")
    key_a = ents[0].get("condition_key") if ents else ""

    # --- 同条件但换了 job_id（真实场景：重跑一次任务）→ 仍应视为同一条
    r = client.post(f"/api/favorites/{fav_id}/dft-entries",
                    json={**base, "job_id": "dft_verify_job_1rerun",
                          "e_bind_kcal": -14.2})
    d2b = r.json() if r.status_code == 200 else {}
    ents = d2b.get("dft_entries") or []
    check(r.status_code == 200 and len(ents) == 1
          and ents[0].get("label") == "A"
          and ents[0].get("e_bind_kcal") == -14.2,
          "同条件换 job_id 重跑 → 仍只有 1 条（job_id 不参与条件指纹）",
          f"HTTP {r.status_code} 实际长度 {len(ents)} "
          f"{json.dumps(ents[0] if ents else {}, ensure_ascii=False)[:200]}")
    key_a = ents[0].get("condition_key") if ents else key_a

    # --- 换 method → 不同指纹 → 新增，标号 B
    r = client.post(f"/api/favorites/{fav_id}/dft-entries",
                    json={**base, "method": "B3LYP-D3", "e_bind_kcal": -10.5})
    d3 = r.json() if r.status_code == 200 else {}
    ents = d3.get("dft_entries") or []
    labels = [e.get("label") for e in ents]
    check(r.status_code == 200 and len(ents) == 2 and labels == ["A", "B"],
          "换 method 再提交 → 变 2 条且标号依次为 A、B",
          f"HTTP {r.status_code} labels={labels}")
    check(len(ents) == 2 and ents[1].get("condition_key") != key_a
          and bool(ents[1].get("condition_key")),
          "两条记录的 condition_key 不同（不同条件=不同指纹）",
          f"keyA={key_a!r} keyB={ents[1].get('condition_key') if len(ents) > 1 else None!r}")
    check(ents[0].get("e_bind_kcal") == -14.2,
          "新增 B 不影响已有 A 的结果",
          f"A 的 e_bind_kcal={ents[0].get('e_bind_kcal') if ents else None!r}")

    # --- 失败记录（带 error 字段）
    r = client.post(f"/api/favorites/{fav_id}/dft-entries", json={
        **base, "job_id": "dft_verify_job_failed", "method": "PBE0",
        "error": "SCF 未收敛", "status": "failed", "e_bind_kcal": None,
    })
    d4 = r.json() if r.status_code == 200 else {}
    ents = d4.get("dft_entries") or []
    labels = [e.get("label") for e in ents]
    check(r.status_code == 200 and len(ents) == 3 and labels == ["A", "B", "C"],
          "再提交一条失败条目（带 error）→ 3 条，标号 A、B、C",
          f"HTTP {r.status_code} labels={labels}")
    check(len(ents) == 3 and ents[2].get("error") == "SCF 未收敛",
          "失败条目的 error 字段被原样保存（便于前端展示失败原因）",
          f"实际: {json.dumps(ents[2] if len(ents) > 2 else {}, ensure_ascii=False)[:300]}")

    # --- 只给 job_id 的条目
    r = client.post(f"/api/favorites/{fav_id}/dft-entries",
                    json={"job_id": "dft_verify_only_job"})
    d5 = r.json() if r.status_code == 200 else {}
    ents = d5.get("dft_entries") or []
    check(r.status_code == 200 and len(ents) == 4,
          "只给 job_id 的条目也能写入（后端按 job 补齐；job 不存在也不报错）",
          f"HTTP {r.status_code} 长度 {len(ents)} 响应 {_snippet(r, 200)}")
    check(len(ents) == 4 and ents[3].get("label") == "D"
          and bool(ents[3].get("mode")),
          "只给 job_id 的条目同样带 label（D）与 mode 字段",
          f"实际: {json.dumps(ents[3] if len(ents) > 3 else {}, ensure_ascii=False)[:300]}")

    # --- 删除「只给 job_id」那条（index 3），验证落盘
    r = client.delete(f"/api/favorites/{fav_id}/dft-entries/3")
    d6 = r.json() if r.status_code == 200 else {}
    ents = d6.get("dft_entries") or []
    labels = [e.get("label") for e in ents]
    check(r.status_code == 200 and len(ents) == 3 and labels == ["A", "B", "C"],
          "DELETE dft-entries/3 → 200，剩 3 条且已有标号 A/B/C 不重排",
          f"HTTP {r.status_code} labels={labels}")
    check(isinstance(d6.get("deleted_entry"), dict)
          and d6.get("deleted_entry", {}).get("job_id") == "dft_verify_only_job",
          "删除响应回带 deleted_entry（前端可提示删了哪条）",
          f"deleted_entry={json.dumps(d6.get('deleted_entry'), ensure_ascii=False)[:200]}"
          if d6 else _detail(r))

    # --- 删除失败记录（index 2，即 C），验证持久化
    r = client.delete(f"/api/favorites/{fav_id}/dft-entries/2")
    d7 = r.json() if r.status_code == 200 else {}
    ents = d7.get("dft_entries") or []
    labels = [e.get("label") for e in ents]
    check(r.status_code == 200 and len(ents) == 2 and labels == ["A", "B"],
          "DELETE dft-entries/2（失败记录）→ 200，剩 2 条（失败记录可删）",
          f"HTTP {r.status_code} labels={labels}")
    r = client.get(f"/api/favorites/{fav_id}")
    g = r.json() if r.status_code == 200 else {}
    ents = g.get("dft_entries") or []
    check(r.status_code == 200 and len(ents) == 2
          and not any(str(e.get("error") or "") for e in ents),
          "重新 GET 收藏：失败记录确实没了（持久化，不是内存假象）",
          f"HTTP {r.status_code} 长度 {len(ents)} "
          f"errors={[e.get('error') for e in ents]}")
    check(g.get("dft_labels") == ["A", "B"],
          "GET 响应里 dft_labels == ['A','B']（响应派生标号与条目一致）",
          f"实际: {g.get('dft_labels')!r}")
    lst = client.get("/api/favorites").json().get("favorites", [])
    mine = next((f for f in lst if f.get("id") == fav_id), {})
    check(mine.get("dft_labels") == ["A", "B"]
          and len(mine.get("dft_entries") or []) == 2,
          "GET /api/favorites 列表端点也按当前条目重算（读路径不受磁盘过期副本影响）",
          f"列表里该收藏: dft_labels={mine.get('dft_labels')!r} "
          f"条目数={len(mine.get('dft_entries') or [])}")
    disk = read_json(fav_path) if fav_path.is_file() else {}
    dentries = disk.get("dft_entries") or []
    check(len(dentries) == 2
          and [e.get("label") for e in dentries] == ["A", "B"]
          and all(e.get("condition_key") for e in dentries),
          "落盘 fav_*.json 里 dft_entries 也是 2 条、标号 A/B、各有 condition_key",
          f"磁盘: {json.dumps(dentries, ensure_ascii=False)[:300]}")
    check(disk.get("dft_snapshot") is None,
          "落盘 dft_snapshot 保持 None（新结构以 dft_entries 为唯一数据源）",
          f"磁盘 dft_snapshot={disk.get('dft_snapshot')!r}")
    # 派生字段口径：读写路径都必须重算，磁盘上即使存在也不允许是过期值
    check(disk.get("archive_type", g.get("archive_type")) == g.get("archive_type")
          and disk.get("dft_labels", g.get("dft_labels")) == g.get("dft_labels"),
          "archive_type / dft_labels 与响应派生值一致（不存在过期残留）",
          f"磁盘 archive_type={disk.get('archive_type')!r} "
          f"dft_labels={disk.get('dft_labels')!r} / 响应 "
          f"{g.get('archive_type')!r} {g.get('dft_labels')!r}",
          kind="consistency")
    leaked = [k for k in ("archive_type", "dft_labels") if k in disk]
    if leaked:
        note("实现细节（与代码注释口径不符，且落盘副本会过期）："
             f"{leaked} 这两个「响应派生」字段实际被写进了 fav_*.json —— "
             "_dft_response_view() 就地改同一份 dict（src/favorites/store.py:733-734），"
             "而写路径（add_dft_entry/delete_dft_entry/update_monomer_meta/"
             "update_favorite）都是 get_favorite() -> _write(fav) 一路写回，"
             "派生值是在**本次改动之前**算出来的，于是磁盘上留下与 dft_entries "
             "自相矛盾的陈旧副本（证据见上方与下方 [FAIL] 行的磁盘/响应对比）。"
             "注意：所有读路径都会重算这两个字段，因此 API 响应永远是对的。"
             f"该文件磁盘键: {sorted(disk)}")

    # --- 越界 / 不存在
    r = client.delete(f"/api/favorites/{fav_id}/dft-entries/9")
    check(r.status_code == 404, "DELETE 越界序号 → 404", _detail(r))
    r = client.delete("/api/favorites/fav_19700101_001/dft-entries/0")
    check(r.status_code == 404, "DELETE 不存在的收藏 → 404", _detail(r))

    # --- 名称/CAS 修改后 DFT 记录仍在
    r = client.patch(f"/api/favorites/{fav_id}/monomer-meta",
                     json={"aldehyde": {"name": "验收醛-二次改名"}})
    after = r.json() if r.status_code == 200 else {}
    check(r.status_code == 200
          and len(after.get("dft_entries") or []) == 2
          and after.get("aldehyde", {}).get("name") == "验收醛-二次改名",
          "再次 PATCH 名称：DFT 计算记录（2 条）不受影响",
          _detail(r))

    # --- 观察项：不同目标分子是否重置标号
    r = client.post(f"/api/favorites/{fav_id}/dft-entries", json={
        **base, "job_id": "dft_verify_job_3", "method": "GFN2-xTB",
        "x_type": "solvent", "x_smiles": "O", "e_bind_kcal": -5.1,
    })
    d8 = r.json() if r.status_code == 200 else {}
    labels = [e.get("label") for e in (d8.get("dft_entries") or [])]
    note("同一张卡里换「目标分子」（x_type/x_smiles 变化，method 相同）后标号="
         f"{labels}（v2.20 用户口径：标号按「单体组 + 目标分子」分组，"
         "各组独立从 A 开始 —— 实现见 src/favorites/store.py::_label_key / "
         "_next_label_after）")
    r = client.delete(f"/api/favorites/{fav_id}/dft-entries/2")
    check(r.status_code == 200, "（清理）删除该观察条目 → 200", _detail(r))

    # --- 标号不复用空档：删掉 A 后新增记录应拿 C（而不是复用 A/B）
    r = client.delete(f"/api/favorites/{fav_id}/dft-entries/0")
    check(r.status_code == 200, "（前置）删除标号 A 的记录 → 200", _detail(r))
    r = client.post(f"/api/favorites/{fav_id}/dft-entries", json={
        **base, "job_id": "dft_verify_job_4", "method": "wB97X-D",
        "e_bind_kcal": -9.4,
    })
    d9 = r.json() if r.status_code == 200 else {}
    labels = [e.get("label") for e in (d9.get("dft_entries") or [])]
    check(r.status_code == 200 and labels == ["B", "C"],
          "删除 A 后新增不同条件记录 → 标号取下一个未占用字母 C（不复用空档，已有标号不重排）",
          f"HTTP {r.status_code} labels={labels}")

    return {"entries": len(d9.get("dft_entries") or [])}


# --------------------------------------------------------------------------- 步骤 4


def step4_diary(client, rec_id: str, diary_store) -> dict:
    """功能 4：科研日记 + mentions 关联 + by-record 反查 + 助手工具 read_diary。"""
    section("功能 4/4：科研日记（CRUD / mentions / by-record 反查 / 助手 read_diary）")

    today = date.today().isoformat()
    token = "ZQ7VERIFY"
    r = client.post("/api/diary", json={
        "date": today,
        "title": "验收日记：XRD 已完成复盘",
        "content": f"# 独立验收\n今天 {token} 记录了 XRD 结果，关联实验记录 @{rec_id}。",
        "mentions": [rec_id],
    })
    check(r.status_code == 201, "POST /api/diary 返回 201", _detail(r))
    if r.status_code != 201:
        return {}
    entry = r.json()
    entry_id = entry.get("entry_id", "")
    check(bool(re.match(r"^dy_[0-9a-f]{12}$", str(entry_id))),
          f"返回的 entry_id 形如 dy_<12 位十六进制>（{entry_id}）")
    check(entry.get("mentions") == [rec_id],
          "日记 mentions 保存了 @ 的实验记录 id",
          f"实际: {entry.get('mentions')!r}")
    md = entry.get("mention_details") or []
    check(len(md) == 1 and md[0].get("record_id") == rec_id
          and md[0].get("exists") is True,
          "响应里 mention_details 展开出该实验记录且 exists=true",
          f"实际: {json.dumps(md, ensure_ascii=False)[:300]}")

    # --- 列表 / 关键词 / by-record
    r = client.get("/api/diary")
    listed = r.json() if r.status_code == 200 else {}
    ids = [e.get("entry_id") for e in listed.get("entries", [])]
    check(r.status_code == 200 and entry_id in ids and listed.get("count", 0) >= 1,
          "GET /api/diary 能读到新建的日记（count/stats 一并返回）",
          f"HTTP {r.status_code} ids={ids} stats={listed.get('stats')}")
    r = client.get("/api/diary", params={"keyword": token})
    ids = [e.get("entry_id") for e in r.json().get("entries", [])] \
        if r.status_code == 200 else []
    check(r.status_code == 200 and entry_id in ids,
          f"GET /api/diary?keyword={token} 能按正文关键词命中",
          f"HTTP {r.status_code} ids={ids}")
    r = client.get("/api/diary", params={"record_id": rec_id})
    ids = [e.get("entry_id") for e in r.json().get("entries", [])] \
        if r.status_code == 200 else []
    check(r.status_code == 200 and entry_id in ids,
          "GET /api/diary?record_id=... 能按关联记录过滤",
          f"HTTP {r.status_code} ids={ids}")
    r = client.get(f"/api/diary/by-record/{rec_id}")
    by = r.json() if r.status_code == 200 else {}
    ids = [e.get("entry_id") for e in by.get("entries", [])]
    check(r.status_code == 200 and by.get("record_id") == rec_id
          and entry_id in ids and by.get("count", 0) >= 1,
          "GET /api/diary/by-record/{record_id} 反查到该日记",
          f"HTTP {r.status_code} 响应 {_snippet(r, 200)}")
    r = client.get("/api/diary/by-record/rec_20200101_999")
    check(r.status_code == 200 and r.json().get("count") == 0,
          "by-record 查一个合法但无关联的记录 → count=0（不是 500/404）",
          _detail(r))
    r = client.get("/api/diary/by-record/not-a-record-id")
    check(r.status_code == 400,
          "by-record 传非法 record_id 格式 → 400", _detail(r))

    # --- mentions 校验：不存在的记录不许假引用
    r = client.post("/api/diary", json={
        "date": today, "title": "假引用", "content": "正文够长即可",
        "mentions": ["rec_20200101_999"],
    })
    check(r.status_code == 400, "mentions 指向不存在的实验记录 → 400（不留假引用）",
          _detail(r))
    r = client.post("/api/diary", json={"date": today, "content": "   "})
    check(r.status_code == 400, "空正文 → 400", _detail(r))
    # mentions 去重（同一条记录 @ 两次不应存两份）
    r = client.post("/api/diary", json={
        "date": today, "title": "去重探测", "content": f"正文 {token}",
        "mentions": [rec_id, rec_id]})
    dedup = r.json() if r.status_code == 201 else {}
    check(r.status_code == 201 and dedup.get("mentions") == [rec_id],
          "mentions 传重复 id → 去重保序只留一份",
          f"HTTP {r.status_code} mentions={dedup.get('mentions')!r}")
    if dedup.get("entry_id"):
        client.delete(f"/api/diary/{dedup['entry_id']}")

    # --- 落盘 JSONL
    dpath = diary_store.diary_path()
    raw = dpath.read_text(encoding="utf-8") if Path(dpath).is_file() else ""
    rows = [json.loads(x) for x in raw.splitlines() if x.strip()]
    hit = [x for x in rows if x.get("entry_id") == entry_id]
    check(Path(dpath).is_file() and len(hit) == 1
          and hit[0].get("mentions") == [rec_id],
          "落盘 diary_entries.jsonl 里能看到该条目与 mentions",
          f"文件={dpath} 命中={json.dumps(hit, ensure_ascii=False)[:300]}")

    # --- 助手工具 read_diary
    second_id = ""
    registry = None
    for name in ("assistant.registry", "src.assistant.registry"):
        try:
            registry = importlib.import_module(name)
            break
        except ImportError:
            continue
    check(registry is not None and "read_diary" in getattr(registry, "TOOLS", {}),
          "助手工具表 registry.TOOLS 里注册了 read_diary",
          f"registry={registry} TOOLS 键={sorted(getattr(registry, 'TOOLS', {}))[:8]}"
          if registry is not None else "registry 模块导入失败")
    if registry is not None:
        res = registry.execute("read_diary", {"keyword": token})
        det = res.get("details") or {}
        check(res.get("is_error") is False and det.get("found") is True
              and det.get("count", 0) >= 1
              and entry_id in (det.get("entry_ids") or []),
              "read_diary(keyword=ZQ7VERIFY) 检索到该日记（结构化 details.found=true）",
              f"结果: {json.dumps(res, ensure_ascii=False)[:400]}")
        check(token in str(res.get("text") or "")
              and str(entry.get("title")) in str(res.get("text") or ""),
              "read_diary 的中文摘要里带上了日记正文/标题（不是空话）",
              f"text={str(res.get('text'))[:200]!r}")
        res = registry.execute("read_diary", {"record_id": rec_id})
        det = res.get("details") or {}
        check(res.get("is_error") is False and det.get("found") is True
              and entry_id in (det.get("entry_ids") or []),
              "read_diary(record_id=...) 能按关联实验记录检索到",
              f"结果: {json.dumps(res, ensure_ascii=False)[:300]}")
        res = registry.execute("read_diary",
                              {"date_from": today, "date_to": today})
        det = res.get("details") or {}
        check(res.get("is_error") is False and det.get("found") is True,
              "read_diary(date_from=date_to=今天) 能按日期检索到",
              f"结果: {json.dumps(res, ensure_ascii=False)[:300]}")
        # 关键词能命中「@ 关联记录」的元信息（实验编号）而不只是日记正文
        res = registry.execute("read_diary", {"keyword": "V220-VERIFY-XRD"})
        det = res.get("details") or {}
        check(res.get("is_error") is False and det.get("found") is True
              and entry_id in (det.get("entry_ids") or []),
              "read_diary 关键词命中关联实验记录的实验编号（@ 关联可检索）",
              f"结果: {json.dumps(res, ensure_ascii=False)[:300]}")
        # limit 生效（先补一条同日日记，保证同条件下有多篇）
        r = client.post("/api/diary", json={
            "date": today, "title": "第二篇", "content": f"第二篇正文 {token}",
            "mentions": [rec_id]})
        second_id = r.json().get("entry_id") if r.status_code == 201 else ""
        res = registry.execute("read_diary",
                              {"keyword": token, "limit": 1})
        det = res.get("details") or {}
        check(res.get("is_error") is False
              and det.get("count") == 1 and det.get("matched", 0) >= 2,
              "read_diary(limit=1) 只返回 1 篇但如实报告 matched>=2",
              f"结果: {json.dumps(res, ensure_ascii=False)[:300]}")
        # --- 无结果：结构化空结果，而不是编造
        res = registry.execute("read_diary", {"keyword": "绝无此词QXZ9871"})
        det = res.get("details") or {}
        text = str(res.get("text") or "")
        check(res.get("is_error") is False and det.get("found") is False
              and det.get("count") == 0 and det.get("total") is not None
              and not det.get("entry_ids"),
              "无结果时 return 结构化空结果（found=false / count=0 / is_error=false）",
              f"结果: {json.dumps(res, ensure_ascii=False)[:400]}")
        check("未查到" in text and "编造" in text,
              "空结果的中文提示明确说明「未查到」且要求不要编造",
              f"text={text[:200]!r}")
        check(token not in text and str(entry.get("title")) not in text,
              "空结果里没有夹带任何真实日记内容（不编造/不串味）",
              f"text={text[:200]!r}")
        res = registry.execute("read_diary", {"record_id": "rec_20200101_999"})
        det = res.get("details") or {}
        check(res.get("is_error") is False and det.get("found") is False,
              "read_diary(不存在的记录 id) → 空结果而非报错",
              f"结果: {json.dumps(res, ensure_ascii=False)[:300]}")
        res = registry.execute("read_diary", {"record_id": "bad-id"})
        check(res.get("is_error") is True,
              "read_diary 参数非法 → is_error=true（不抛异常炸助手循环）",
              f"结果: {json.dumps(res, ensure_ascii=False)[:300]}")

    # --- 更新
    r = client.put(f"/api/diary/{entry_id}", json={"title": "验收日记：已更新标题"})
    check(r.status_code == 200
          and r.json().get("title") == "验收日记：已更新标题"
          and r.json().get("mentions") == [rec_id],
          "PUT /api/diary/{id} 局部更新标题且 mentions 保留",
          _detail(r))
    r = client.get(f"/api/diary/{entry_id}")
    check(r.status_code == 200
          and r.json().get("title") == "验收日记：已更新标题",
          "重新 GET 日记，更新已持久化", _detail(r))

    # --- 删除（用另一条临时日记，避免影响上面的反查结论）
    r = client.post("/api/diary", json={
        "date": today, "title": "待删除", "content": f"临时内容 {token}"})
    tmp_id = r.json().get("entry_id") if r.status_code == 201 else ""
    r = client.delete(f"/api/diary/{tmp_id}")
    check(r.status_code == 200, "DELETE /api/diary/{id} 返回 200", _detail(r))
    r = client.get(f"/api/diary/{tmp_id}")
    check(r.status_code == 404, "删除后 GET 该日记 → 404", _detail(r))
    raw = Path(dpath).read_text(encoding="utf-8") if Path(dpath).is_file() else ""
    check(tmp_id not in raw, "删除后 JSONL 落盘文件里也不再有该条目",
          f"文件内容里仍能搜到 {tmp_id}")
    if second_id:
        client.delete(f"/api/diary/{second_id}")
    r = client.get(f"/api/diary/by-record/{rec_id}")
    check(r.status_code == 200
          and entry_id in [e.get("entry_id") for e in r.json().get("entries", [])],
          "删除临时日记后，by-record 反查原日记仍然成立",
          _detail(r))

    return {"entry_id": entry_id}


# --------------------------------------------------------------------------- 主流程

#: 需要确认「没被本脚本写入」的仓库真实数据目录
REAL_WATCH_DIRS = (
    REPO_ROOT / "data" / "rag_export" / "records",
    REPO_ROOT / "data" / "favorites",
    REPO_ROOT / "data" / "diary",
    REPO_ROOT / "data" / "experiment_records",
    REPO_ROOT / "data" / "literature",
    REPO_ROOT / "data" / "assistant",
)


def main() -> int:
    print("=" * 78)
    print("v2.20 四功能独立验收（真实 FastAPI 路由 + 落盘双重校验）")
    print("=" * 78)

    tmp_root = bootstrap()
    info(f"临时数据根（COF_DATA_DIR）: {tmp_root}")
    info(f"仓库根: {REPO_ROOT}")

    real_before = snapshot_dirs(REAL_WATCH_DIRS)

    from fastapi.testclient import TestClient  # noqa: E402

    from api.main import app  # noqa: E402

    from src import runtime_config  # noqa: E402

    data_root = runtime_config.user_data_root()
    check(path_inside(data_root, tmp_root),
          "COF_DATA_DIR 生效：runtime_config.user_data_root() 落在临时目录内",
          f"实际: {data_root}（期望在 {tmp_root} 下）")

    already, repinned = pin_store_paths(data_root)
    check(not repinned,
          "records/favorites 的导入期路径常量本就指向临时目录（导入顺序正确）",
          "需要运行时重定向: " + "; ".join(repinned) if repinned else "")
    for line in already:
        info(f"路径常量已在隔离目录: {line}")
    for line in repinned:
        info(f"路径常量已被运行时重定向到隔离目录: {line}")

    rec_store = importlib.import_module("records.store")
    fav_store = importlib.import_module("favorites.store")
    diary_store = importlib.import_module("src.diary.store")
    for mod, attr in ((rec_store, "RECORDS_DIR"), (rec_store, "ATTACHMENTS_DIR"),
                      (fav_store, "FAVORITES_DIR")):
        check(path_inside(getattr(mod, attr), tmp_root),
              f"{mod.__name__}.{attr} 落在临时目录内",
              f"实际: {getattr(mod, attr)}")
    check(path_inside(diary_store.diary_path(), tmp_root),
          "diary store 落盘路径落在临时目录内（路径每次调用重算）",
          f"实际: {diary_store.diary_path()}")

    # 不进 with：不触发 lifespan（避免加载真实 dft_jobs.json / 预热嵌入模型）
    client = TestClient(app)

    r = client.get("/api/health")
    check(r.status_code == 200, "GET /api/health 存活", _detail(r))

    ctx1 = step1_records(client, rec_store)
    ctx2 = step2_archive_card(client, fav_store)
    if ctx2:
        step3_dft_entries(client, fav_store, ctx2["fav_id"], ctx2["fav_path"])
    if ctx1.get("rec_id"):
        step4_diary(client, ctx1["rec_id"], diary_store)

    # --- 真实数据目录未被写入（红线）
    real_after = snapshot_dirs(REAL_WATCH_DIRS)
    added = sorted(set(real_after) - set(real_before))
    changed = sorted(k for k in set(real_after) & set(real_before)
                     if real_after[k] != real_before[k])
    check(not added and not changed,
          "仓库真实 data/ 相关目录未被本脚本写入（运行前后快照一致）",
          f"新增={added[:5]} 改动={changed[:5]}")

    # --- 汇总
    print("\n" + "=" * 78)
    feat_fail = [d for k, d in _FAILURES if k == "feature"]
    cons_fail = [d for k, d in _FAILURES if k == "consistency"]
    print(f"功能验收点（v2.20 四项功能）：{_COUNTS['feature'] - len(feat_fail)}"
          f"/{_COUNTS['feature']} 通过")
    print(f"附加数据一致性检查：{_COUNTS['consistency'] - len(cons_fail)}"
          f"/{_COUNTS['consistency']} 通过")
    if _FAILURES:
        print(f"\n结果 = FAIL（共 {_CHECKS} 项检查，{len(_FAILURES)} 项未通过）")
        for kind, desc in _FAILURES:
            tag = "功能验收" if kind == "feature" else "数据一致性"
            print(f"  - [{tag}] {desc}")
    else:
        print(f"\n结果 = ALL PASS（共 {_CHECKS} 项检查全部通过）")
    print("=" * 78)
    info(f"临时数据目录: {tmp_root}")
    return 1 if _FAILURES else 0


def _cleanup(tmp_root: Path, keep: bool) -> None:
    if keep:
        info(f"--keep：保留临时数据目录 {tmp_root}")
        return
    shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    _keep = "--keep" in sys.argv[1:]
    try:
        _code = main()
    except Exception:
        print("\n[FAIL] 验收脚本自身异常（未跑完）")
        traceback.print_exc()
        _code = 2
    if TMP_ROOT is not None:
        _cleanup(TMP_ROOT, _keep)
    sys.exit(_code)
