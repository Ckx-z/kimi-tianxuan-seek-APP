/**
 * 「AI 识别全部」的**纯逻辑**（v2.0.2）：逐张队列的选图 / 进度 / 取消 / 汇总。
 *
 * 背景：批量视觉识别原先是一次 POST 到后端批量端点，界面上只有「已耗时 + 转圈」，
 * 不可取消、没有逐张进度。现在前端改为**逐张调单图端点**，于是「跳过已标注的图」
 * 「单张失败不中断」「取消后停止后续张（已完成的保留）」这三条语义都落在本模块里。
 *
 * 为什么单独一层：本文件**零 import、零副作用**（不碰 React、不碰 fetch、不碰 toast），
 * 所以可以直接被 Node 的类型擦除加载做单测 —— 见 `tests/test_figure_caption_queue_v202.py`
 * （该测试加载的就是本文件本身，不是复制出来的 JS 逻辑）。
 *
 * 跳过的口径与后端 `src/literature/figure_caption.py` 的 `DONE_CAPTION_SOURCES`
 * 保持一致：`caption_source ∈ {vision, user}` 默认不重做。
 */

/** 已有这些来源的图默认不重做（force=true 才重做；与后端同口径） */
export const DONE_CAPTION_SOURCES = ['vision', 'user'] as const;

/** 队列只关心这两件事：图的 id 与它的标注来源 */
export interface CaptionCandidate {
  fig_id: string;
  /** pdf（原文抽取）| vision（视觉标注）| user（人工）| none */
  caption_source?: string | null;
}

/** 单张失败的原因（原样保留后端给的中文 error/detail） */
export interface CaptionError {
  fig_id: string;
  error: string;
}

/** 逐张队列的进度：每张完成后由 recordCaptionResult 累加 */
export interface CaptionRun {
  /** 本次计划处理的张数（= 选出的 todo 数） */
  total: number;
  /** 已处理完的张数（成功 + 失败） */
  finished: number;
  succeeded: number;
  failed: number;
  /** 因已有 vision/user 标注而跳过 */
  skipped: number;
  /** 超出单次上限、本次未处理 */
  deferred: number;
  cancelled: boolean;
  errors: CaptionError[];
}

/** 单张的处理结果（由调用方把 HTTP 响应/异常翻译成这个形状） */
export interface CaptionOutcome {
  fig_id: string;
  ok: boolean;
  error?: string | null;
}

export function isCaptionDone(source: string | null | undefined): boolean {
  const s = (source ?? '').trim().toLowerCase();
  return (DONE_CAPTION_SOURCES as readonly string[]).includes(s);
}

/**
 * 选图：把「没有视觉/人工标注」的图排成待处理队列。
 *
 * - `force=true` 才重做已标注的图（默认 false = 与后端批量端点同一跳过口径）；
 * - `limit` 只作用于**真正要处理**的图（跳过的图不占额度），超出的记入 deferred。
 */
export function selectCaptionTargets<T extends CaptionCandidate>(
  figures: readonly T[],
  opts: { force?: boolean; limit?: number } = {},
): { todo: T[]; skipped: T[]; deferred: T[] } {
  const force = opts.force === true;
  const limit = typeof opts.limit === 'number' && opts.limit > 0
    ? Math.floor(opts.limit)
    : 0;
  const todo: T[] = [];
  const skipped: T[] = [];
  const deferred: T[] = [];
  for (const fig of figures) {
    if (!force && isCaptionDone(fig.caption_source)) {
      skipped.push(fig);
      continue;
    }
    if (limit > 0 && todo.length >= limit) {
      deferred.push(fig);
      continue;
    }
    todo.push(fig);
  }
  return { todo, skipped, deferred };
}

/** 选图 + 初始进度（total 只算真正要处理的张数） */
export function planCaptionRun<T extends CaptionCandidate>(
  figures: readonly T[],
  opts: { force?: boolean; limit?: number } = {},
): { todo: T[]; skipped: T[]; deferred: T[]; run: CaptionRun } {
  const { todo, skipped, deferred } = selectCaptionTargets(figures, opts);
  return {
    todo,
    skipped,
    deferred,
    run: {
      total: todo.length,
      finished: 0,
      succeeded: 0,
      failed: 0,
      skipped: skipped.length,
      deferred: deferred.length,
      cancelled: false,
      errors: [],
    },
  };
}

/** 记录一张的结果：失败只累加计数与原因，**不影响其余张**（调用方继续循环） */
export function recordCaptionResult(run: CaptionRun, outcome: CaptionOutcome): CaptionRun {
  const finished = run.finished + 1;
  if (outcome.ok) {
    return { ...run, finished, succeeded: run.succeeded + 1 };
  }
  const error = (outcome.error ?? '').trim() || '视觉读图失败（后端未给出原因）';
  return {
    ...run,
    finished,
    failed: run.failed + 1,
    errors: [...run.errors, { fig_id: outcome.fig_id, error }],
  };
}

export function markCaptionCancelled(run: CaptionRun): CaptionRun {
  return { ...run, cancelled: true };
}

/** 进度文案：第 i / n 张（i = 已完成，n = 本次计划张数） */
export function captionProgressLabel(run: CaptionRun): string {
  return `第 ${run.finished} / ${run.total} 张`;
}

/** 失败原因摘要（默认最多列 2 条，其余折叠为计数） */
export function captionErrorBrief(run: CaptionRun, max = 2): string {
  const shown = run.errors.slice(0, Math.max(1, max)).map((e) => e.error);
  const rest = run.errors.length - shown.length;
  return shown.join('；') + (rest > 0 ? `；等共 ${run.errors.length} 张失败` : '');
}

/** 正常跑完的汇总（成功/跳过/失败分别计数与原因） */
export function captionRunSummary(run: CaptionRun): string {
  const parts = [`成功 ${run.succeeded} 张`];
  if (run.skipped) parts.push(`跳过 ${run.skipped} 张（已有视觉/人工标注）`);
  if (run.failed) parts.push(`失败 ${run.failed} 张`);
  if (run.deferred) parts.push(`另有 ${run.deferred} 张超出单次上限未处理`);
  let text = `AI 识别完成：${parts.join('，')}`;
  if (run.failed) text += `；原因：${captionErrorBrief(run)}`;
  return text;
}

/** 取消后的汇总：**已完成的保留**，只说明停在了第几张 */
export function captionCancelledSummary(run: CaptionRun): string {
  let text = `已取消，完成 ${run.finished}/${run.total} 张`
    + `（成功 ${run.succeeded}，失败 ${run.failed}）`;
  if (run.skipped) text += `；跳过 ${run.skipped} 张（已有视觉/人工标注）`;
  if (run.failed) text += `；原因：${captionErrorBrief(run)}`;
  return text;
}

/** 没有任何图需要处理时的提示 */
export function captionNothingToDoMessage(run: CaptionRun): string {
  let text = `没有需要识别的图：跳过 ${run.skipped} 张（已有视觉/人工标注）`;
  if (run.deferred) text += `，另有 ${run.deferred} 张超出单次上限未处理`;
  return text;
}

/** 取消标记：由组件持有（点击「取消」按钮即置 true） */
export interface CaptionQueueHooks<T extends CaptionCandidate> {
  /** 处理单张（把 HTTP 响应/异常翻译成 CaptionOutcome；异常也应转成 ok:false） */
  caption: (fig: T) => Promise<CaptionOutcome>;
  /** 返回 true 则**不再处理后续张**（当前张跑完即停；已完成的保留，不回滚） */
  shouldCancel?: () => boolean;
  /** 每张处理完后回调（组件用来更新「第 i / n 张」并刷新该图） */
  onProgress?: (run: CaptionRun, fig: T) => void | Promise<void>;
  force?: boolean;
  limit?: number;
}

/**
 * 逐张执行队列：**单张失败不中断整批**；`shouldCancel()` 为真时停止后续张，
 * 返回的 run 里 `cancelled=true`、`finished` 就是「已完成」的张数。
 */
export async function runCaptionQueue<T extends CaptionCandidate>(
  figures: readonly T[],
  hooks: CaptionQueueHooks<T>,
): Promise<CaptionRun> {
  const { todo, run: initial } = planCaptionRun(figures, {
    force: hooks.force,
    limit: hooks.limit,
  });
  let run = initial;
  for (const fig of todo) {
    if (hooks.shouldCancel?.()) {
      return markCaptionCancelled(run);
    }
    let outcome: CaptionOutcome;
    try {
      outcome = await hooks.caption(fig);
    } catch (err) {
      // caption 自己抛异常也不许打断整批：如实记成这一张失败
      outcome = {
        fig_id: fig.fig_id,
        ok: false,
        error: err instanceof Error ? err.message : String(err),
      };
    }
    run = recordCaptionResult(run, outcome);
    await hooks.onProgress?.(run, fig);
  }
  // 全部跑完就不算「取消」（避免出现「已取消，完成 3/3 张」这种自相矛盾的提示）
  if (run.finished < run.total && hooks.shouldCancel?.()) {
    return markCaptionCancelled(run);
  }
  return run;
}
