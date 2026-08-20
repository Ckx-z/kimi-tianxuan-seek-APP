/**
 * 工具调用事件卡片：在消息流中默认折叠，展开可见参数/结果摘要。
 * tool_call → 🔧 调用中样式；tool_result → 成功/失败摘要。
 */
import { useState } from 'react';
import { ChevronRight, Loader2, Wrench, CircleCheck, CircleAlert } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { ToolEvent } from './api';

/** 工具名 → 中文标签（未知工具回退原名） */
const TOOL_LABEL: Record<string, string> = {
  query_graph: '查询图谱',
  list_experiment_records: '查询实验记录',
  predict: '预测打分',
  search_literature: '检索文献',
};

interface ToolEventCardProps {
  event: ToolEvent;
  /** 是否仍在流式进行中（tool_call 尚无对应 result） */
  pending?: boolean;
}

export function ToolEventCard({ event, pending = false }: ToolEventCardProps) {
  const [open, setOpen] = useState(false);
  const label = TOOL_LABEL[event.name] ?? event.name;
  const isResult = event.type === 'tool_result';
  const hasDetail =
    (event.args && Object.keys(event.args).length > 0) || Boolean(event.summary);

  return (
    <div
      className={cn(
        'rounded-lg border text-xs',
        event.is_error
          ? 'border-destructive/40 bg-destructive/5'
          : 'border-gold/40 bg-gold-muted/40',
      )}
    >
      <button
        type="button"
        onClick={() => hasDetail && setOpen((v) => !v)}
        className={cn(
          'flex w-full items-center gap-2 px-3 py-2 text-left',
          hasDetail ? 'cursor-pointer' : 'cursor-default',
        )}
      >
        {pending ? (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-gold" />
        ) : isResult ? (
          event.is_error ? (
            <CircleAlert className="h-3.5 w-3.5 shrink-0 text-destructive" />
          ) : (
            <CircleCheck className="h-3.5 w-3.5 shrink-0 text-gold" />
          )
        ) : (
          <Wrench className="h-3.5 w-3.5 shrink-0 text-gold" />
        )}
        <span className="text-foreground/90">
          {isResult ? `${label} · 返回结果` : `🔧 ${label}…`}
        </span>
        {hasDetail && (
          <ChevronRight
            className={cn(
              'ml-auto h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform',
              open && 'rotate-90',
            )}
          />
        )}
      </button>
      {open && hasDetail && (
        <div className="space-y-1.5 border-t border-gold/30 px-3 py-2 text-muted-foreground">
          {event.args && Object.keys(event.args).length > 0 && (
            <pre className="overflow-x-auto rounded bg-muted/60 p-2 font-mono text-[11px]">
              {JSON.stringify(event.args, null, 2)}
            </pre>
          )}
          {event.summary && <p className="leading-relaxed">{event.summary}</p>}
        </div>
      )}
    </div>
  );
}
