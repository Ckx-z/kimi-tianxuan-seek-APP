"""页⑤「方案迭代」路由：建议生成 / 列表 / 采纳 / 方案列表。

把原 Gradio App 页⑤的迭代逻辑下沉为 REST 接口，供 React 前端对接：

- GET  /api/iterate/suggestions   读 data/rag_export/suggestions/sug_*.json
- POST /api/iterate/suggest       subprocess 调编排器 iterate_suggest.py
- POST /api/iterate/adopt         采纳建议 → generated_plans.adopt_suggestion
- GET  /api/iterate/plans         读 data/generated_plans/plan_*.json

编排器协作契约（钉死，勿改）：
    <编排器解释器> minimax/adapters/iterate_suggest.py
        [--favorite-id ID] [--record-id rec_YYYYMMDD_NNN] --question "文本"
        --app-root <项目根>
解释器经 src/runtime_config 解析（环境变量 COF_GRAPHRAG_PYTHON >
config/runtime.local.json > 自动探测）；解析不到时降级为主进程内
import 编排器执行；显式配置但路径不存在 → 503 而非崩溃。
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

# 运行时环境配置（项目根已入 sys.path；src/ 直接上 path 时走兜底）
try:
    from src import runtime_config
except ImportError:  # pragma: no cover
    import runtime_config  # type: ignore
SUGGESTIONS_DIR = runtime_config.user_data_root() / "rag_export" / "suggestions"
PLANS_DIR = runtime_config.user_data_root() / "generated_plans"
# 编排器的 --app-root：其契约是 <app_root>/data/...，frozen 时指向可写的
# 用户应用根（%APPDATA%/COF-Film-Recommend），包内 _MEIPASS 只读不能写
ORCHESTRATOR_APP_ROOT = runtime_config.user_app_root()

# 编排器解释器：经 runtime_config 解析（环境变量 > runtime.local.json >
# 探测）；解析不到为 None，此时降级为主进程内 import 编排器。
# 保持模块级变量以便测试 monkeypatch（契约不变）。
_resolved = runtime_config.graphrag_python()
ITERATE_PYTHON = str(_resolved) if _resolved is not None else None
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


def _run_suggest_inprocess(args: list[str]) -> dict:
    """主进程内 import 编排器并运行（未配置外部解释器时的降级通道）。

    GraphRAG 底座（networkx + graph.pkl）主环境已具备时无需
    E:\\python3.12 即可生成建议。编排器 err_exit 的 SystemExit 映射为
    HTTP 错误；stdout 末行 JSON 摘要契约与 subprocess 通道一致。
    """
    import contextlib
    import importlib.util
    import io
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutTimeout

    ok, reason = runtime_config.graphrag_inprocess_available()
    if not ok:
        raise HTTPException(
            503, "迭代建议生成暂不可用：GraphRAG 主进程内执行条件不满足"
            f"（{reason}），且未配置外部编排器解释器。可在 "
            "config/runtime.local.json 或环境变量 COF_GRAPHRAG_PYTHON 中指定。")

    spec = importlib.util.spec_from_file_location(
        "cof_iterate_suggest_inproc", ITERATE_SCRIPT)
    if spec is None or spec.loader is None:
        raise HTTPException(503, "迭代建议生成暂不可用：编排器模块加载失败。")
    mod = importlib.util.module_from_spec(spec)
    buf = io.StringIO()

    def _invoke():
        spec.loader.exec_module(mod)
        old_argv = sys.argv
        sys.argv = ["iterate_suggest.py"] + args
        try:
            with contextlib.redirect_stdout(buf):
                mod.main()
        finally:
            sys.argv = old_argv

    # 线程内执行以便套用与 subprocess 一致的超时口径；超时后线程可能仍在
    # 后台写盘（与旧契约的「超时却写成功」提示一致），不强行 kill。
    ex = ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(_invoke)
    try:
        fut.result(timeout=ITERATE_TIMEOUT_S)
    except _FutTimeout:
        raise HTTPException(
            504, f"建议生成超时（>{ITERATE_TIMEOUT_S}s）：编排器可能仍在后台"
            "写入，请稍后刷新建议列表确认是否已落盘。")
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        if code != 0:
            raise HTTPException(
                500, f"建议生成失败（编排器退出码 {code}，主进程内执行）。")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            500, f"建议生成失败（主进程内执行异常）：{type(exc).__name__}: {exc}")
    finally:
        ex.shutdown(wait=False)

    stdout = buf.getvalue().strip()
    if not stdout:
        raise HTTPException(500, "建议生成失败：编排器（主进程内）stdout 为空。")
    try:
        return json.loads(stdout.splitlines()[-1])
    except Exception as exc:
        raise HTTPException(
            500, f"建议生成失败：编排器结果摘要解析失败（{exc}）。")


@router.post("/suggest")
def run_suggest(req: SuggestRequest):
    """生成迭代建议：成功返回 {written, count, batch}。

    执行通道：已配置外部解释器 → subprocess（原契约）；未配置（None）→
    主进程内 import 编排器；显式配置了但路径不存在 → 明确 503。
    """
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "question（问题描述）不能为空")

    if not ITERATE_SCRIPT.exists():
        raise HTTPException(
            503, "迭代建议生成暂不可用：编排器脚本 "
            "minimax/adapters/iterate_suggest.py 未就位。")

    # 组装编排器参数（两通道共用）；--app-root 显式传项目根，
    # 避免依赖编排器内的开发机默认路径
    args = ["--question", question, "--app-root", str(ORCHESTRATOR_APP_ROOT)]
    fav = (req.favorite_id or "").strip()
    rec = (req.record_id or "").strip()
    if rec:
        # 锚定记录 id 格式校验：不符契约 → 400（不放行给编排器）
        if not _RECORD_ID_RE.match(rec):
            raise HTTPException(
                400, "record_id 格式非法：应为 rec_YYYYMMDD_NNN，"
                f"收到 {rec!r}")
    if fav:
        args += ["--favorite-id", fav]
    if rec:
        # 与 favorite-id 可同传；favorite 缺省时编排器从记录推断
        args += ["--record-id", rec]

    if ITERATE_PYTHON is None:
        # 未配置外部解释器 → 主进程内 import 降级通道
        summary = _run_suggest_inprocess(args)
        return {
            "written": summary.get("written") or [],
            "count": summary.get("count") or 0,
            "batch": summary.get("batch"),
        }

    # 显式配置的解释器不存在 → 明确 503（不要静默降级掩盖配置错误）
    if not Path(ITERATE_PYTHON).exists():
        raise HTTPException(
            503, "迭代建议生成暂不可用：找不到编排器解释器 "
            f"{ITERATE_PYTHON}。请确认 python3.12 环境已安装，"
            "或在 config/runtime.local.json / 环境变量 COF_GRAPHRAG_PYTHON "
            "中修正路径。")

    cmd = [ITERATE_PYTHON, str(ITERATE_SCRIPT)] + args

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
