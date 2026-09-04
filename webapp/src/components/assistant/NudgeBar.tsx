/**
 * 主动提醒条（V2.2 连续失败 + v1.6.0 P2 新失误记录）
 * - 进入助手页自动拉取 GET /api/assistant/nudges（同日同收藏只提醒一次，
 *   已 dismiss 的由后端过滤）
 * - 连续失败：点击 → 通过 onPrefill 把分析话术填入对话输入框（确认后发送）
 * - 新失误记录：可「回顾讨论」（预填输入框）或「深度研究」（跳转助手并开启
 *   深度研究模式、预填研究问题）
 * - 「知道了」→ POST /nudges/dismiss，当日不再提醒该收藏
 */
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { AlertTriangle, Check, MessageSquareText, Microscope } from 'lucide-react';
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
  if (n.kind === 'new_mistake') {
    return `帮我复盘「${n.monomers}」最近一次实验的失误（${n.latest_mistakes}）并结合历史记录给出改进方向`;
  }
  return `帮我分析 ${n.monomers} 连续失败的原因并给出改进方案`;
}

export function NudgeBar({ onPrefill, disabled }: NudgeBarProps) {
  const navigate = useNavigate();
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

  /** 深度研究：跳转助手页并开启研究模式、预填研究问题 */
  const goDeepResearch = useCallback((n: AssistantNudge) => {
    navigate('/assistant', {
      state: {
        researchMode: true,
        openingMessage:
          `对「${n.monomers}」最近一次失败（失误：${n.latest_mistakes}）做深度归因研究：结合系统内历史实验、知识图谱证据与文献，给出失败原因假设（标注置信度）与下一步改进方向。`,
      },
    });
  }, [navigate]);

  if (nudges.length === 0) return null;

  return (
    <div className="space-y-2">
      {nudges.map((n) => (
        <div
          key={`${n.kind ?? 'failure'}:${n.favorite_id}`}
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
                {n.kind === 'new_mistake'
                  ? `${n.monomers} 新填写了失误记录`
                  : `${n.monomers} 已连续失败 ${n.consecutive_failures ?? 2} 次`}
              </span>
              <span className="block truncate text-muted-foreground">
                失误：{n.latest_mistakes}
              </span>
              {n.kind === 'new_mistake' ? (
                <span className="mt-0.5 inline-flex items-center gap-1 text-[11px] text-amber-700 dark:text-amber-400">
                  <MessageSquareText className="h-3 w-3" />
                  点击让 ming 复盘，或发起深度研究
                </span>
              ) : (
                <span className="mt-0.5 inline-flex items-center gap-1 text-[11px] text-amber-700 dark:text-amber-400">
                  <MessageSquareText className="h-3 w-3" />
                  点击让 ming 分析失败原因
                </span>
              )}
            </span>
          </button>
          {n.kind === 'new_mistake' && (
            <Button
              size="sm"
              variant="outline"
              disabled={disabled}
              onClick={() => goDeepResearch(n)}
              className="h-7 shrink-0 px-2 text-xs"
              title="开启深度研究模式做失败归因"
            >
              <Microscope className="mr-1 h-3.5 w-3.5" />
              深度研究
            </Button>
          )}
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
