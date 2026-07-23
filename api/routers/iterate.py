"""页⑤「方案迭代」路由：建议生成 / 列表 / 采纳 / 方案列表。

把原 Gradio App 页⑤的迭代逻辑下沉为 REST 接口，供 React 前端对接：

- GET  /api/iterate/suggestions   读 data/rag_export/suggestions/sug_*.json
- POST /api/iterate/suggest       subprocess 调编排器 iterate_suggest.py
- POST /api/iterate/adopt         采纳建议 → generated_plans.adopt_suggestion
- GET  /api/iterate/plans         读 data/generated_plans/plan_*.json

编排器协作契约（钉死，勿改）：
    E:\\python3.12\\python.exe minimax/adapters/iterate_suggest.py
        [--favorite-id ID] [--record-id rec_YYYYMMDD_NNN] --question "文本"
成功 exit 0 且 stdout 末行 {"written": [...], "count": N, "batch": "..."}。
--record-id 可选、与 --favorite-id 可同传；favorite 缺省时编排器从记录的
favorite_id 推断。不传 --record-id 的现有行为完全不变。
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..schemas import AdoptRequest, SuggestRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/iterate", tags=["iterate"])

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# generated_plans 内部以 `from src.recommend import ...` 方式导入，
# 需项目根在 sys.path（deps 只加了 src/，此处补根目录）
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SUGGESTIONS_DIR = PROJECT_ROOT / "data" / "rag_export" / "suggestions"
PLANS_DIR = PROJECT_ROOT / "data" / "generated_plans"

# 钉死的协作契约：编排器解释器路径、脚本路径与超时（LLM 主备串行最坏
# 约 240s，留 60s 余量防「超时却写成功」）
ITERATE_PYTHON = r"E:\python3.12\python.exe"
ITERATE_SCRIPT = PROJECT_ROOT / "minimax" / "adapters" / "iterate_suggest.py"
ITERATE_TIMEOUT_S = 300

# 锚定记录 id 格式（与编排器契约一致）：rec_YYYYMMDD_NNN
_RECORD_ID_RE = re.compile(r"^rec_\d{8}_\d{3}$")


def _load_json_list(directory: Path, pattern: str, what: str) -> list[dict]:
    """扫描目录下 JSON 文件列表；损坏文件跳过（容错，不影响整体）。"""
    items: list[dict] = []
    if not directory.exists():
        return items
    for p in sorted(directory.glob(pattern)):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                items.append(data)
        except Exception as exc:
            logger.warning("跳过损坏的%s文件 %s: %s", what, p.name, exc)
    return items


@router.get("/suggestions")
def list_suggestions(favorite_id: str | None = None,
                     status: str | None = None,
                     batch: str | None = None):
    """迭代建议列表：按 created_at 倒序；可选 favorite_id/status/batch 过滤。

    example.json 为格式样例文件，不进入列表。
    """
    sugs = _load_json_list(SUGGESTIONS_DIR, "sug_*.json", "建议")
    if favorite_id:
        sugs = [s for s in sugs if str(s.get("favorite_id") or "") == favorite_id]
    if status:
        sugs = [s for s in sugs if str(s.get("status") or "") == status]
    if batch:
        sugs = [s for s in sugs if str(s.get("batch") or "") == batch]
    sugs.sort(key=lambda s: str(s.get("created_at") or ""), reverse=True)
    return {"suggestions": sugs, "count": len(sugs)}


@router.post("/suggest")
def run_suggest(req: SuggestRequest):
    """生成迭代建议：subprocess 调编排器，成功返回 {written, count, batch}。"""
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "question（问题描述）不能为空")

    # 解释器不存在 → 明确 503（不要静默走通配错误）
    if not Path(ITERATE_PYTHON).exists():
        raise HTTPException(
            503, "迭代建议生成暂不可用：找不到编排器解释器 "
            f"{ITERATE_PYTHON}。请确认 python3.12 环境已安装，"
            "或联系维护者核对路径。")
    if not ITERATE_SCRIPT.exists():
        raise HTTPException(
            503, "迭代建议生成暂不可用：编排器脚本 "
            "minimax/adapters/iterate_suggest.py 未就位。")

    cmd = [ITERATE_PYTHON, str(ITERATE_SCRIPT), "--question", question]
    fav = (req.favorite_id or "").strip()
    rec = (req.record_id or "").strip()
    if rec:
        # 锚定记录 id 格式校验：不符契约 → 400（不放行给编排器）
        if not _RECORD_ID_RE.match(rec):
            raise HTTPException(
                400, "record_id 格式非法：应为 rec_YYYYMMDD_NNN，"
                f"收到 {rec!r}")
    if fav:
        cmd += ["--favorite-id", fav]
    if rec:
        # 与 favorite-id 可同传；favorite 缺省时编排器从记录推断
        cmd += ["--record-id", rec]

    try:
        proc = subprocess.run(
            cmd, cwd=str(PROJECT_ROOT), timeout=ITERATE_TIMEOUT_S,
            capture_output=True, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        raise HTTPException(
            504, f"建议生成超时（>{ITERATE_TIMEOUT_S}s）：编排器可能仍在后台"
            "写入，请稍后刷新建议列表确认是否已落盘。")

    if proc.returncode != 0:
        err = (proc.stderr or "").strip() or "（无 stderr 输出）"
        raise HTTPException(
            500, f"建议生成失败（编排器退出码 {proc.returncode}）："
            f"{err[-800:]}")

    # 契约：stdout 末行为 JSON 摘要 {"written": [...], "count": N, ...}
    stdout = (proc.stdout or "").strip()
    if not stdout:
        raise HTTPException(500, "建议生成失败：编排器 stdout 为空，"
                            "未返回结果摘要。")
    last_line = stdout.splitlines()[-1]
    try:
        summary = json.loads(last_line)
    except Exception as exc:
        raise HTTPException(
            500, f"建议生成失败：编排器结果摘要解析失败（{exc}），"
            f"stdout 末行: {last_line[:400]}")
    return {
        "written": summary.get("written") or [],
        "count": summary.get("count") or 0,
        "batch": summary.get("batch"),
    }


@router.post("/adopt")
def adopt_suggestion(req: AdoptRequest):
    """采纳建议 → 生成编号方案卡（幂等由后端 adopt_suggestion 保证）。"""
    if not req.suggestion_id.strip():
        raise HTTPException(400, "suggestion_id 不能为空")
    try:
        from src.recommend.generated_plans import AdoptError, adopt_suggestion
        plan = adopt_suggestion(
            req.suggestion_id.strip(), template_id=req.template_id or None)
    except AdoptError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"采纳失败：{type(exc).__name__}: {exc}")
    return plan


@router.get("/plans")
def list_plans(favorite_id: str | None = None):
    """已生成方案卡列表：按 created_at 倒序；可选 favorite_id 过滤。"""
    plans = _load_json_list(PLANS_DIR, "plan_*.json", "方案")
    if favorite_id:
        plans = [p for p in plans
                 if str(p.get("favorite_id") or "") == favorite_id]
    plans.sort(key=lambda p: str(p.get("created_at") or ""), reverse=True)
    return {"plans": plans, "count": len(plans)}
