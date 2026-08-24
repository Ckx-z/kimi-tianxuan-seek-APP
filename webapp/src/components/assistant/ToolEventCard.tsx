/**
 * 工具调用事件卡片：在消息流中默认折叠，展开可见参数/结果摘要。
 * tool_call → 🔧 调用中样式；tool_result → 成功/失败摘要；
 * tool_confirm → 写操作二次确认卡（影响说明 + 「确认执行」「取消」按钮）。
 */
import { useState } from 'react';
import { ChevronRight, Loader2, Wrench, CircleCheck, CircleAlert, ShieldAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import type { ToolEvent } from './api';

/** 工具名 → 中文标签（未知工具回退原名） */
const TOOL_LABEL: Record<string, string> = {
  query_graph: '查询图谱',
  list_experiment_records: '查询实验记录',
  predict: '预测打分',
  search_literature: '检索文献',
  predict_film: '成膜打分',
  query_graphrag: '图谱 / 文献检索',
  read_experiment_records: '查询实验记录',
  list_favorites: '查看收藏',
  list_prediction_history: '打分历史',
  manage_favorite: '收藏管理',
  generate_plan_card: '生成方案卡',
  draft_experiment_record: '起草实验记录',
  query_dft: 'DFT 计算',
};

export type ConfirmDecision = 'confirm' | 'cancel';

interface ToolEventCardProps {
  event: ToolEvent;
  /** 是否仍在流式进行中（tool_call 尚无对应 result） */
  pending?: boolean;
  /** tool_confirm：用户点了「确认执行」/「取消」 */
  onConfirmDecision?: (event: ToolEvent, decision: ConfirmDecision) => void;
  /** tool_confirm：确认请求进行中（防重复点击） */
  confirmBusy?: boolean;
}

export function ToolEventCard({
  event,
  pending = false,
  onConfirmDecision,
  confirmBusy = false,
}: ToolEventCardProps) {
  const [open, setOpen] = useState(false);
  const label = TOOL_LABEL[event.name] ?? event.name;

  // ---------- 写操作二次确认卡 ----------
  if (event.type === 'tool_confirm') {
    const resolved = event.resolved;
    const actionable = !resolved && Boolean(onConfirmDecision);
    return (
      <div className="rounded-lg border border-gold/60 bg-gold-muted/50 text-xs">
        <div className="flex items-center gap-2 px-3 py-2">
          <ShieldAlert className="h-3.5 w-3.5 shrink-0 text-gold" />
          <span className="font-medium text-foreground">需要确认：{label}</span>
          {resolved === 'confirmed' && (
            <span className="ml-auto rounded-full bg-gold/20 px-2 py-0.5 text-[11px] text-gold-foreground">
              已确认执行
            </span>
          )}
          {resolved === 'cancelled' && (
            <span className="ml-auto rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
              已取消
            </span>
          )}
          {resolved === 'history' && (
            <span className="ml-auto rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
              历史确认请求
            </span>
          )}
        </div>
        {event.impact && (
          <p className="px-3 pb-1.5 leading-relaxed text-muted-foreground">{event.impact}</p>
        )}
        {(event.args_summary || (event.args && Object.keys(event.args).length > 0)) && (
          <div className="px-3 pb-1.5">
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="flex items-center gap-1 text-muted-foreground/80 hover:text-foreground"
            >
              <ChevronRight
                className={cn('h-3 w-3 transition-transform', open && 'rotate-90')}
              />
              参数摘要
            </button>
            {open && (
              <pre className="mt-1 overflow-x-auto rounded bg-muted/60 p-2 font-mono text-[11px] text-muted-foreground">
                {event.args_summary || JSON.stringify(event.args, null, 2)}
              </pre>
            )}
          </div>
        )}
        {actionable && (
          <div className="flex gap-2 border-t border-gold/30 px-3 py-2">
            <Button
              size="sm"
              disabled={confirmBusy}
              onClick={() => onConfirmDecision?.(event, 'confirm')}
              className="h-7 bg-primary px-3 text-xs text-primary-foreground"
            >
              {confirmBusy && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
              确认执行
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={confirmBusy}
              onClick={() => onConfirmDecision?.(event, 'cancel')}
              className="h-7 px-3 text-xs"
            >
              取消
            </Button>
            <span className="ml-auto self-center text-[11px] text-muted-foreground/70">
              5 分钟内有效
            </span>
          </div>
        )}
      </div>
    );
  }

  // ---------- 普通工具调用 / 结果卡 ----------
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
          {isResult
            ? event.cancelled
              ? `${label} · 已取消`
              : `${label} · 返回结果`
            : `🔧 ${label}…`}
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
