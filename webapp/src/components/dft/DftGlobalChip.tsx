/**
 * DFT 全局任务悬浮徽标：所有页面可见（右下角固定）。
 * - pending/running：金色脉冲 + 实时进度提示，点击返回 DFT 计算页；
 * - done：绿色「查看结果」，点击跳转并关闭徽标；
 * - failed/interrupted：红色提示，点击跳转查看原因。
 */
import { useNavigate } from 'react-router';
import { CheckCircle2, Loader2, TriangleAlert, X } from 'lucide-react';
import { useDftTask } from './DftTaskContext';

export default function DftGlobalChip() {
  const { task, clearTask } = useDftTask();
  const navigate = useNavigate();
  if (!task) return null;

  const terminal =
    task.status === 'done' || task.status === 'failed' || task.status === 'interrupted';

  const handleClick = () => {
    navigate('/toolbox/dft');
    if (terminal) clearTask();
  };

  const colorClass =
    task.status === 'done'
      ? 'border-green-300 bg-green-50 text-green-800 dark:border-green-800 dark:bg-green-950 dark:text-green-300'
      : terminal
        ? 'border-red-300 bg-red-50 text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-300'
        : 'border-gold/60 bg-background/95 text-foreground shadow-lg backdrop-blur';

  const label =
    task.status === 'done'
      ? `DFT 完成 · ${task.summary}`
      : task.status === 'failed'
        ? `DFT 失败 · ${task.summary}`
        : task.status === 'interrupted'
          ? `DFT 已中断 · ${task.summary}`
          : task.progressPercent > 0
            ? `DFT 计算中 ${task.progressPercent}% · ${task.progressHint}`
            : `DFT 计算中 · ${task.progressHint}`;

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={handleClick}
      onKeyDown={(e) => { if (e.key === 'Enter') handleClick(); }}
      className={`fixed bottom-4 right-4 z-50 flex max-w-sm cursor-pointer items-center gap-2 rounded-full border px-3 py-2 text-xs transition-colors ${colorClass}`}
      title={terminal ? '点击查看结果' : '点击返回 DFT 计算页'}
    >
      {terminal ? (
        task.status === 'done'
          ? <CheckCircle2 className="h-4 w-4 shrink-0" />
          : <TriangleAlert className="h-4 w-4 shrink-0" />
      ) : (
        <Loader2 className="h-4 w-4 shrink-0 animate-spin" />
      )}
      <span className="truncate">{label}</span>
      {terminal && (
        <span
          role="button"
          tabIndex={0}
          onClick={(e) => { e.stopPropagation(); clearTask(); }}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); clearTask(); } }}
          className="rounded-full p-0.5 hover:bg-black/10 dark:hover:bg-white/10"
          title="关闭提示"
        >
          <X className="h-3.5 w-3.5" />
        </span>
      )}
    </div>
  );
}
