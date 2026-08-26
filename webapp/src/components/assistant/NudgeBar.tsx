/**
 * 连续失败提醒条（V2.2 主动能力二）
 * - 进入助手页自动拉取 GET /api/assistant/nudges（同日同收藏只提醒一次，
 *   已 dismiss 的由后端过滤）
 * - 点击提醒 → 通过 onPrefill 把分析话术填入对话输入框（不自动发送，用户确认后发）
 * - 「知道了」→ POST /nudges/dismiss，当日不再提醒该收藏
 */
import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, Check, MessageSquareText } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  assistantApi,
  AssistantUnavailableError,
  type AssistantNudge,
} from './api';

interface NudgeBarProps {
  /** 点击提醒后把分析话术填入对话输入框（不自动发送） */
  onPrefill: (text: string) => void;
  /** 会话流式进行中禁用交互（避免打断） */
  disabled?: boolean;
}

export function nudgePrompt(n: AssistantNudge): string {
  return `帮我分析 ${n.monomers} 连续失败的原因并给出改进方案`;
}

export function NudgeBar({ onPrefill, disabled }: NudgeBarProps) {
  const [nudges, setNudges] = useState<AssistantNudge[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await assistantApi.nudges();
        if (!cancelled) setNudges(list);
      } catch {
        // 拉取失败静默（提醒是辅助能力，不阻塞对话）
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const dismiss = useCallback(async (n: AssistantNudge) => {
    if (busyId) return;
    setBusyId(n.favorite_id);
    // 乐观移除，失败回滚
    setNudges((ns) => ns.filter((x) => x.favorite_id !== n.favorite_id));
    try {
      const list = await assistantApi.dismissNudge(n.favorite_id);
      setNudges(list);
    } catch (e) {
      setNudges((ns) => [...ns, n]);
      toast.error(
        e instanceof AssistantUnavailableError
          ? '无法连接后端服务，dismiss 未生效'
          : `dismiss 失败：${e instanceof Error ? e.message : '未知错误'}`,
      );
    } finally {
      setBusyId(null);
    }
  }, [busyId]);

  if (nudges.length === 0) return null;

  return (
    <div className="space-y-2">
      {nudges.map((n) => (
        <div
          key={n.favorite_id}
          className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-amber-500/40 bg-amber-500/10 px-4 py-2.5"
        >
          <button
            type="button"
            disabled={disabled}
            onClick={() => onPrefill(nudgePrompt(n))}
            title="点击把分析请求填入输入框（确认后发送）"
            className="flex min-w-0 flex-1 items-start gap-2.5 text-left disabled:opacity-60"
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
            <span className="min-w-0 text-xs leading-relaxed">
              <span className="font-medium text-foreground">
                {n.monomers} 已连续失败 {n.consecutive_failures} 次
              </span>
              <span className="block truncate text-muted-foreground">
                最近失误：{n.latest_mistakes}
              </span>
              <span className="mt-0.5 inline-flex items-center gap-1 text-[11px] text-amber-700 dark:text-amber-400">
                <MessageSquareText className="h-3 w-3" />
                点击让 ming 分析失败原因
              </span>
            </span>
          </button>
          <Button
            size="sm"
            variant="ghost"
            disabled={disabled || busyId === n.favorite_id}
            onClick={() => dismiss(n)}
            className="h-7 shrink-0 px-2 text-xs text-muted-foreground"
          >
            <Check className="mr-1 h-3.5 w-3.5" />
            知道了
          </Button>
        </div>
      ))}
    </div>
  );
}
