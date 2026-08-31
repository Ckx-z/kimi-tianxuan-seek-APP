/**
 * DFT 全局任务状态（v1.2.1+ 增强）：跨页面任务进度与完成通知。
 *
 * 与页面内状态（Dft.tsx）并行：页面内负责表单与结果面板，本 Context 负责
 * 「全局可见」——悬浮徽标（DftGlobalChip）在任何页面都能展示当前任务状态；
 * 后台每 5s 轮询 GET /api/dft/jobs/{id}，状态转入终态（done/failed/interrupted）
 * 时弹 toast + 浏览器 Notification，并停止轮询（chip 保留终态供点击查看）。
 *
 * 恢复语义（避免重复初始化）：Provider 挂在 App 顶层，路由切换不重建；
 * 应用启动时只读一次草稿（resumedRef 防 StrictMode 双跑），若草稿带
 * currentJobId 则恢复全局跟踪（F5 / 重开应用场景）。
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { ReactNode } from 'react';
import { toast } from 'sonner';
import {
  fetchDftDraft,
  fetchDftJob,
  type DftBackend,
  type DftJob,
  type DftJobStatus,
} from './api';

const POLL_INTERVAL_MS = 5000;
const MAX_CONSECUTIVE_FAILURES = 3;

export interface GlobalDftTask {
  jobId: string;
  status: DftJobStatus;
  progressHint: string;
  /** 展示用摘要（如「苯甲醛 × 苯胺」） */
  summary: string;
  backend?: DftBackend;
  submittedAt: number;
}

export interface TrackOptions {
  summary?: string;
  backend?: DftBackend;
  cached?: boolean;
  /** 提交即终态时传入（如缓存命中），避免先显示「排队中」 */
  initialStatus?: DftJobStatus;
}

interface DftTaskContextValue {
  /** 当前全局任务（无任务为 null） */
  task: GlobalDftTask | null;
  /** 登记新任务并启动全局轮询；终态任务仅展示并通知，不轮询 */
  trackTask: (jobId: string, opts?: TrackOptions) => void;
  /** 清除全局任务展示（不取消后端计算） */
  clearTask: () => void;
}

const DftTaskContext = createContext<DftTaskContextValue | null>(null);

function isTerminal(status: DftJobStatus): boolean {
  return status === 'done' || status === 'failed' || status === 'interrupted';
}

function notifyCompletion(status: DftJobStatus, cached: boolean, summary: string): void {
  let msg: string;
  if (status === 'done') {
    msg = cached ? '命中缓存，已返回历史结果' : `「${summary}」DFT 计算完成，可查看结果`;
  } else if (status === 'failed') {
    msg = `「${summary}」DFT 计算失败，请到计算页查看原因`;
  } else {
    msg = `「${summary}」DFT 任务已中断，请到计算页确认`;
  }
  if (status === 'done') toast.success(msg);
  else toast.error(msg);
  if (typeof Notification !== 'undefined' && Notification.permission === 'granted') {
    try {
      new Notification('COF 科研助手', { body: msg });
    } catch {
      // 通知失败不影响主流程
    }
  }
}

export function DftTaskProvider({ children }: { children: ReactNode }) {
  const [task, setTask] = useState<GlobalDftTask | null>(null);
  const taskRef = useRef<GlobalDftTask | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const failCountRef = useRef(0);
  const resumedRef = useRef(false);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const applyJob = useCallback((job: DftJob) => {
    const prev = taskRef.current;
    if (!prev || prev.jobId !== job.job_id) return;
    failCountRef.current = 0;
    const next: GlobalDftTask = {
      ...prev,
      status: job.status,
      progressHint: job.progress_hint || prev.progressHint,
    };
    // 通知只在「非终态 → 终态」转变时触发（StrictMode 下也不会重复）
    const transitioned = isTerminal(job.status) && !isTerminal(prev.status);
    taskRef.current = next;
    setTask(next);
    if (isTerminal(job.status)) stopPolling();
    if (transitioned) notifyCompletion(job.status, job.cached, next.summary);
  }, [stopPolling]);

  const refreshOnce = useCallback(async (jobId: string) => {
    try {
      const job = await fetchDftJob(jobId);
      applyJob(job);
    } catch {
      failCountRef.current += 1;
      // 连续多次失败（后端重启且任务已清理等）：停止轮询并明确提示
      if (failCountRef.current >= MAX_CONSECUTIVE_FAILURES) {
        const prev = taskRef.current;
        if (prev && prev.jobId === jobId && !isTerminal(prev.status)) {
          const next: GlobalDftTask = {
            ...prev,
            status: 'interrupted',
            progressHint: '任务状态查询失败（后端可能已重启），请到计算页确认',
          };
          taskRef.current = next;
          setTask(next);
          stopPolling();
          notifyCompletion('interrupted', false, next.summary);
        }
      }
    }
  }, [applyJob, stopPolling]);

  const startPolling = useCallback((jobId: string) => {
    stopPolling();
    failCountRef.current = 0;
    void refreshOnce(jobId); // 立即刷一次，避免等满一个间隔
    pollRef.current = setInterval(() => { void refreshOnce(jobId); }, POLL_INTERVAL_MS);
  }, [stopPolling, refreshOnce]);

  const trackTask = useCallback((jobId: string, opts: TrackOptions = {}) => {
    const t: GlobalDftTask = {
      jobId,
      status: opts.initialStatus ?? 'pending',
      progressHint: opts.initialStatus === 'done'
        ? (opts.cached ? '命中缓存，直接返回历史结果' : '计算完成')
        : '已提交，排队中…',
      summary: opts.summary ?? 'DFT 计算',
      backend: opts.backend,
      submittedAt: Date.now(),
    };
    taskRef.current = t;
    setTask(t);
    if (isTerminal(t.status)) {
      stopPolling();
      notifyCompletion(t.status, opts.cached ?? false, t.summary);
    } else {
      startPolling(jobId);
    }
    // 用户手势内请求浏览器通知权限（仅首次询问，Electron/浏览器均支持）
    if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
      try { void Notification.requestPermission(); } catch { /* 忽略 */ }
    }
  }, [startPolling, stopPolling]);

  const clearTask = useCallback(() => {
    stopPolling();
    taskRef.current = null;
    setTask(null);
  }, [stopPolling]);

  // 应用启动：读草稿恢复「上次进行中任务」的全局跟踪（只执行一次）
  useEffect(() => {
    if (resumedRef.current) return;
    resumedRef.current = true;
    let cancelled = false;
    fetchDftDraft()
      .then(({ draft }) => {
        if (cancelled || !draft?.currentJobId) return;
        const jobId = draft.currentJobId;
        const t: GlobalDftTask = {
          jobId,
          status: 'pending',
          progressHint: '恢复任务状态…',
          summary: 'DFT 计算',
          submittedAt: Date.now(),
        };
        taskRef.current = t;
        setTask(t);
        void refreshOnce(jobId).then(() => {
          if (cancelled) return;
          const cur = taskRef.current;
          if (cur && cur.jobId === jobId && !isTerminal(cur.status)) {
            startPolling(jobId);
          }
        });
      })
      .catch(() => {});
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Provider 卸载时停止轮询
  useEffect(() => () => stopPolling(), [stopPolling]);

  const value = useMemo<DftTaskContextValue>(
    () => ({ task, trackTask, clearTask }),
    [task, trackTask, clearTask],
  );

  return (
    <DftTaskContext.Provider value={value}>{children}</DftTaskContext.Provider>
  );
}

export function useDftTask(): DftTaskContextValue {
  const ctx = useContext(DftTaskContext);
  if (!ctx) throw new Error('useDftTask 必须在 DftTaskProvider 内使用');
  return ctx;
}
