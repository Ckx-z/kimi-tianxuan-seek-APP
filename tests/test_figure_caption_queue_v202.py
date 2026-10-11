"""v2.0.2（需求 2）：前端「AI 识别全部」逐张队列纯逻辑的真实单测。

被测对象是**交付代码本身**：`webapp/src/components/mine/figureCaptionQueue.ts`
（零 import、零副作用的纯模块，被 `LiteratureFiguresPanel.tsx` 直接用）。测试用 Node 的
TS 类型擦除加载该文件（不是另抄一份 JS 逻辑），覆盖：

1. 跳过口径：`caption_source ∈ {vision, user}` 不重做（与后端批量端点同口径）；
   `force=true` 才重做；`limit` 只作用于真正要处理的图，超出的记为 deferred；
2. 逐张失败不中断：第 2 张返回 `ok:false`（后端中文原因）、第 3 张直接抛异常（如 500），
   队列仍继续到第 4 张；`onProgress` 每张回调一次且 `finished` 递增（= 界面「第 i / n 张」）；
3. 取消语义：`shouldCancel()` 为真后**不再处理后续张**，已完成的保留（不回滚），
   `finished/total` 就是「已取消，完成 x/n 张」里的数字；全部跑完时不算取消；
4. 汇总文案：成功/跳过/失败分别计数，失败附后端给的原因。

Node 不可用 / 版本过低（不支持类型擦除）时 **skip** 并说明原因 —— 不伪造测试。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
QUEUE_MODULE = (PROJECT_ROOT / "webapp" / "src" / "components" / "mine"
                / "figureCaptionQueue.ts")

#: harness：加载真实 TS 模块 → 跑场景 → 把原始结果写成 JSON（断言全在 Python 侧）
_HARNESS = r"""
import { writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';

const mod = await import(pathToFileURL(process.env.QUEUE_MODULE).href);
const out = {};

const figures = [
  { fig_id: 'f1', caption_source: 'pdf' },
  { fig_id: 'f2', caption_source: 'vision' },
  { fig_id: 'f3', caption_source: 'user' },
  { fig_id: 'f4', caption_source: 'none' },
  { fig_id: 'f5' },
];

// A) 选图：跳过已标注（vision/user），limit 只作用于待处理图
const planA = mod.planCaptionRun(figures, { limit: 2 });
out.planA = {
  todo: planA.todo.map((f) => f.fig_id),
  skipped: planA.skipped.map((f) => f.fig_id),
  deferred: planA.deferred.map((f) => f.fig_id),
  run: planA.run,
};
const planForce = mod.planCaptionRun(figures, { force: true });
out.planForce = {
  todo: planForce.todo.map((f) => f.fig_id),
  skipped: planForce.skipped.map((f) => f.fig_id),
};
out.isDone = {
  visionUpper: mod.isCaptionDone('VISION'),
  userSpaced: mod.isCaptionDone(' user '),
  pdf: mod.isCaptionDone('pdf'),
  none: mod.isCaptionDone('none'),
  missing: mod.isCaptionDone(undefined),
};
out.nothingToDo = mod.captionNothingToDoMessage(
  mod.planCaptionRun([{ fig_id: 'x', caption_source: 'vision' }]).run);

// B) 逐张：一张返回失败、一张抛异常，队列都必须继续
const seenB = [];
const progressB = [];
const runB = await mod.runCaptionQueue(
  [
    { fig_id: 'b1', caption_source: 'pdf' },
    { fig_id: 'b2', caption_source: 'pdf' },
    { fig_id: 'b3', caption_source: 'vision' },   // 已有视觉标注 → 跳过，不该被调用
    { fig_id: 'b4', caption_source: 'none' },
  ],
  {
    limit: 10,
    caption: async (fig) => {
      seenB.push(fig.fig_id);
      if (fig.fig_id === 'b2') {
        return { fig_id: 'b2', ok: false,
                 error: '视觉模型未启用：请到「设置 → 文献解析 LLM → 视觉读图」打开开关' };
      }
      if (fig.fig_id === 'b4') {
        throw new Error('请求失败（500）');
      }
      return { fig_id: fig.fig_id, ok: true };
    },
    onProgress: (run, fig) => {
      progressB.push([fig.fig_id, run.finished, run.total]);
    },
  },
);
out.runB = runB;
out.seenB = seenB;
out.progressB = progressB;
out.summaryB = mod.captionRunSummary(runB);
out.progressLabelB = mod.captionProgressLabel(runB);

// C) 取消：处理完第 2 张后点「取消」→ 只处理 2 张，后 2 张不动，已完成的保留
const seenC = [];
const runC = await mod.runCaptionQueue(
  [
    { fig_id: 'c1', caption_source: 'pdf' },
    { fig_id: 'c2', caption_source: 'pdf' },
    { fig_id: 'c3', caption_source: 'pdf' },
    { fig_id: 'c4', caption_source: 'pdf' },
  ],
  {
    caption: async (fig) => {
      seenC.push(fig.fig_id);
      return {
        fig_id: fig.fig_id,
        ok: fig.fig_id !== 'c2',
        error: 'HTTP 500：模型服务不可用',
      };
    },
    onProgress: () => {},
    shouldCancel: () => seenC.length >= 2,
  },
);
out.runC = runC;
out.seenC = seenC;
out.cancelSummaryC = mod.captionCancelledSummary(runC);

// D) 取消发生在第一张之前 → 一张都不处理
const seenD = [];
const runD = await mod.runCaptionQueue(
  [{ fig_id: 'd1', caption_source: 'pdf' }, { fig_id: 'd2', caption_source: 'pdf' }],
  {
    caption: async (fig) => {
      seenD.push(fig.fig_id);
      return { fig_id: fig.fig_id, ok: true };
    },
    shouldCancel: () => true,
  },
);
out.runD = runD;
out.seenD = seenD;
out.cancelSummaryD = mod.captionCancelledSummary(runD);

// E) 全部跑完时（取消请求发生在最后一张之后）不算「取消」
let doneE = 0;
const runE = await mod.runCaptionQueue(
  [{ fig_id: 'e1', caption_source: 'pdf' }],
  {
    caption: async (fig) => {
      doneE += 1;
      return { fig_id: fig.fig_id, ok: true };
    },
    shouldCancel: () => doneE >= 1,
  },
);
out.runE = runE;

await writeFile(process.env.QUEUE_OUT, JSON.stringify(out), 'utf8');
"""

#: 判为「环境不支持」的报错特征（其余非零退出视为真失败，不 skip）
_INFRA_MARKERS = (
    "ERR_UNSUPPORTED_NODE_VERSION",
    "Unknown file extension",
    "ERR_UNKNOWN_FILE_EXTENSION",
    "not supported",
    "Unexpected token 'export'",
)


@pytest.fixture(scope="module")
def queue_results(tmp_path_factory) -> dict:
    """用 Node 加载真实 TS 模块跑一遍场景，返回原始结果。"""
    if not QUEUE_MODULE.is_file():  # pragma: no cover - 文件被删才可能发生
        pytest.skip(f"缺少被测模块 {QUEUE_MODULE}")
    node = shutil.which("node")
    if not node:  # pragma: no cover - 本机有 node（tsc 也依赖它）
        pytest.skip("未找到 node，无法加载 TS 纯模块（不伪造测试）")

    work = tmp_path_factory.mktemp("caption_queue")
    harness = work / "harness.mjs"
    harness.write_text(_HARNESS, encoding="utf-8")
    out_file = work / "out.json"
    env = {
        "QUEUE_MODULE": str(QUEUE_MODULE),
        "QUEUE_OUT": str(out_file),
        "PATH": __import__("os").environ.get("PATH", ""),
    }
    attempts = [[node, str(harness)], [node, "--experimental-strip-types", str(harness)]]
    proc = None
    for args in attempts:
        proc = subprocess.run(args, cwd=str(work), env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=180)
        if proc.returncode == 0:
            break
    assert proc is not None
    if proc.returncode != 0:
        blob = f"{proc.stdout}\n{proc.stderr}"
        if any(marker in blob for marker in _INFRA_MARKERS):
            pytest.skip("本机 node 不支持 TS 类型擦除（--experimental-strip-types 亦不可用）："
                        + blob.strip().splitlines()[-1][:200])
        pytest.fail(f"node 加载被测模块失败（非环境问题）：\n{blob}")
    return json.loads(out_file.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 1. 选图 / 跳过口径

def test_skips_already_captioned_and_honours_limit(queue_results):
    plan = queue_results["planA"]
    # f2(vision) / f3(user) 跳过；f1/f4 待处理；f5 超出 limit=2 → deferred
    assert plan["todo"] == ["f1", "f4"]
    assert plan["skipped"] == ["f2", "f3"]
    assert plan["deferred"] == ["f5"]
    run = plan["run"]
    assert run["total"] == 2 and run["skipped"] == 2 and run["deferred"] == 1
    assert run["finished"] == 0 and run["succeeded"] == 0 and run["failed"] == 0
    assert run["cancelled"] is False and run["errors"] == []


def test_force_redoes_everything(queue_results):
    forced = queue_results["planForce"]
    assert forced["todo"] == ["f1", "f2", "f3", "f4", "f5"]
    assert forced["skipped"] == []


def test_is_caption_done_is_case_insensitive(queue_results):
    flags = queue_results["isDone"]
    assert flags == {"visionUpper": True, "userSpaced": True, "pdf": False,
                     "none": False, "missing": False}


def test_nothing_to_do_message(queue_results):
    text = queue_results["nothingToDo"]
    assert "没有需要识别的图" in text and "跳过 1 张" in text


# ---------------------------------------------------------------- 2. 逐张失败不中断

def test_single_failure_does_not_break_the_batch(queue_results):
    run, seen = queue_results["runB"], queue_results["seenB"]
    # b3（已有视觉标注）从未被调用；b2 失败、b4 抛异常后**仍继续**
    assert seen == ["b1", "b2", "b4"]
    assert run["total"] == 3 and run["skipped"] == 1
    assert run["finished"] == 3          # 1 成功 + 2 失败，全部计入进度
    assert run["succeeded"] == 1 and run["failed"] == 2
    assert run["cancelled"] is False
    # 失败原因如实保留后端给的中文；抛异常的那张也记下原因
    assert "视觉读图" in run["errors"][0]["error"]
    assert run["errors"][1]["fig_id"] == "b4"
    assert run["errors"][1]["error"] == "请求失败（500）"


def test_progress_fires_per_figure_for_i_of_n(queue_results):
    assert queue_results["progressB"] == [["b1", 1, 3], ["b2", 2, 3], ["b4", 3, 3]]
    assert queue_results["progressLabelB"] == "第 3 / 3 张"


def test_summary_counts_success_skip_failure_with_reason(queue_results):
    text = queue_results["summaryB"]
    assert "成功 1 张" in text
    assert "跳过 1 张（已有视觉/人工标注）" in text
    assert "失败 2 张" in text
    assert "视觉读图" in text and "请求失败（500）" in text


# ---------------------------------------------------------------- 3. 取消语义

def test_cancel_stops_remaining_and_keeps_finished(queue_results):
    run, seen = queue_results["runC"], queue_results["seenC"]
    assert seen == ["c1", "c2"]          # c3 / c4 未被处理
    assert run["cancelled"] is True
    assert run["finished"] == 2 and run["total"] == 4
    assert run["succeeded"] == 1 and run["failed"] == 1   # c1 的成功保留（不回滚）
    assert queue_results["cancelSummaryC"].startswith("已取消，完成 2/4 张")
    assert "成功 1" in queue_results["cancelSummaryC"]
    assert "模型服务不可用" in queue_results["cancelSummaryC"]


def test_cancel_before_first_figure_processes_nothing(queue_results):
    assert queue_results["seenD"] == []
    run = queue_results["runD"]
    assert run["cancelled"] is True and run["finished"] == 0 and run["total"] == 2
    assert queue_results["cancelSummaryD"].startswith("已取消，完成 0/2 张")


def test_finished_queue_is_not_reported_as_cancelled(queue_results):
    """取消请求发生在最后一张之后 → 如实报「已完成」，不谎称取消。"""
    run = queue_results["runE"]
    assert run["cancelled"] is False
    assert run["finished"] == run["total"] == 1 and run["failed"] == 0
