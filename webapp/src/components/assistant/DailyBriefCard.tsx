/**
 * 今日科研日报卡（V2.2 主动能力一）
 * - 进入助手页自动拉取 GET /api/assistant/daily-brief（缺省今天），可手动刷新
 * - 结构化展示：新建/更新实验记录、DFT 任务与最佳结合能、新收藏、新录入文献
 * - LLM 已配置且点评生成成功时附 ming 点评段落；未配置时只展示结构化数据
 */
import { useCallback, useEffect, useState } from 'react';
import {
  Atom,
  BookOpen,
  CalendarDays,
  FlaskConical,
  RefreshCw,
  Sparkles,
  Star,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import {
  assistantApi,
  AssistantUnavailableError,
  type DailyBrief,
} from './api';

/** 结果徽标配色（与契约 outcome 取值一致） */
function outcomeBadge(r: { outcome: string; outcome_zh: string }) {
  const cls =
    r.outcome === 'film'
      ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
      : r.outcome === 'failed'
        ? 'border-destructive/40 bg-destructive/10 text-destructive'
        : 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400';
  return (
    <Badge variant="outline" className={cn('shrink-0 text-[10px] font-normal', cls)}>
      {r.outcome_zh}
    </Badge>
  );
}

export function DailyBriefCard() {
  const [brief, setBrief] = useState<DailyBrief | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setBrief(await assistantApi.dailyBrief());
    } catch (e) {
      setBrief(null);
      setError(
        e instanceof AssistantUnavailableError
          ? '无法连接后端服务'
          : e instanceof Error
            ? e.message
            : '日报加载失败',
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const hasAny =
    !!brief &&
    (brief.records_created_count > 0 ||
      brief.records_updated_count > 0 ||
      brief.dft_count > 0 ||
      brief.favorites_count > 0 ||
      brief.literature_count > 0);

  return (
    <section className="rounded-xl border border-gold/30 bg-card shadow-sm">
      {/* 卡头：标题 + 日期 + 刷新 */}
      <header className="flex items-center justify-between gap-2 border-b border-border/60 px-4 py-2.5">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md gradient-royal">
            <CalendarDays className="h-3.5 w-3.5 text-white" />
          </span>
          <h2 className="truncate text-sm font-semibold text-foreground">
            今日科研日报
          </h2>
          {brief && (
            <span className="shrink-0 text-xs text-muted-foreground">{brief.date}</span>
          )}
        </div>
        <Button
          size="sm"
          variant="ghost"
          onClick={load}
          disabled={loading}
          className="h-7 shrink-0 px-2 text-xs text-muted-foreground"
          title="刷新日报"
        >
          <RefreshCw className={cn('mr-1 h-3.5 w-3.5', loading && 'animate-spin')} />
          刷新
        </Button>
      </header>

      <div className="space-y-3 px-4 py-3">
        {loading && !brief ? (
          <div className="space-y-2">
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-4 w-1/2" />
          </div>
        ) : error ? (
          <p className="text-xs text-muted-foreground">
            日报加载失败：{error}（可点右上角刷新重试）
          </p>
        ) : brief ? (
          <>
            {/* 指标行 */}
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span className="inline-flex items-center gap-1">
                <FlaskConical className="h-3.5 w-3.5 text-gold" />
                新建记录 <b className="text-foreground">{brief.records_created_count}</b>
              </span>
              <span className="inline-flex items-center gap-1">
                更新记录 <b className="text-foreground">{brief.records_updated_count}</b>
              </span>
              <span className="inline-flex items-center gap-1">
                <Atom className="h-3.5 w-3.5 text-gold" />
                DFT 任务 <b className="text-foreground">{brief.dft_count}</b>
              </span>
              <span className="inline-flex items-center gap-1">
                <Star className="h-3.5 w-3.5 text-gold" />
                新收藏 <b className="text-foreground">{brief.favorites_count}</b>
              </span>
              <span className="inline-flex items-center gap-1">
                <BookOpen className="h-3.5 w-3.5 text-gold" />
                新文献 <b className="text-foreground">{brief.literature_count}</b>
              </span>
              {brief.dft_best_e_bind_kcal !== null && (
                <span className="inline-flex items-center gap-1">
                  最佳结合能
                  <b className="text-foreground">{brief.dft_best_e_bind_kcal} kcal/mol</b>
                  （半经验）
                </span>
              )}
            </div>

            {!hasAny && (
              <p className="text-xs text-muted-foreground">
                今天还没有新的科研活动记录，开工后这里会自动汇总。
              </p>
            )}

            {/* 新建实验记录清单 */}
            {brief.records_created.length > 0 && (
              <ul className="space-y-1">
                {brief.records_created.map((r) => (
                  <li
                    key={r.record_id}
                    className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs"
                  >
                    {outcomeBadge(r)}
                    <span className="font-medium text-foreground">{r.monomers}</span>
                    {r.experiment_no && (
                      <span className="text-muted-foreground">#{r.experiment_no}</span>
                    )}
                    {r.self_summary && (
                      <span className="w-full pl-1 text-muted-foreground">
                        {r.self_summary}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}

            {/* ming 点评（LLM 配置且生成成功时） */}
            {brief.commentary && (
              <p className="rounded-lg border border-gold/25 bg-gold-muted/40 px-3 py-2 text-xs leading-relaxed text-foreground">
                <Sparkles className="mr-1 inline h-3.5 w-3.5 text-gold" />
                {brief.commentary}
              </p>
            )}
          </>
        ) : null}
      </div>
    </section>
  );
}
