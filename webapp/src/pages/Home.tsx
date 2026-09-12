/**
 * 首页仪表盘（完整实现）
 * - 四张统计卡：收藏数 / 实验记录数 / 方案数 / 建议数
 * - 最近实验记录（前 5 条）
 * - 最新批次建议预览（最新 batch 的 2 条）
 * - 后端健康状态指示（绿点在线 / 灰点未连接）
 * - 后端未启动时优雅降级，不白屏
 */
import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router';
import { Star, FlaskConical, ClipboardList, Lightbulb, ArrowRight } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty';
import { Skeleton } from '@/components/ui/skeleton';
import { KpiCard, type KpiCardProps } from '@/components/home/KpiCard';
import { PageHeader } from '@/components/layout/PageHeader';
import { favoritesApi, healthApi, iterateApi, recordsApi, BackendUnavailableError } from '@/lib/api';
import type { ExperimentRecord, Favorite, Plan, Suggestion } from '@/types';
import { experimentTime } from '@/components/records/meta';
import { confidenceLevel } from '@/lib/format';

/**
 * 近 N 周新增计数（v1.9.4 第 3 批：首页仪表盘的真实趋势序列）。
 * 取实验时间（experiment_date 派生的 timeline 首点），跳过未来时间与无法解析的日期。
 */
function weeklyCounts(records: ExperimentRecord[], weeks = 8): number[] {
  const buckets = new Array(weeks).fill(0) as number[];
  const now = Date.now();
  records.forEach((r) => {
    const raw = experimentTime(r).date;
    if (!raw) return;
    const t = new Date(`${raw}T00:00:00`).getTime();
    if (Number.isNaN(t)) return;
    const days = Math.floor((now - t) / 86_400_000);
    if (days < 0) return;
    const idx = weeks - 1 - Math.floor(days / 7);
    if (idx >= 0 && idx < weeks) buckets[idx] += 1;
  });
  return buckets;
}

/**
 * 置信度徽章：与「方案迭代」卡同口径 —— `payload.confidence` 是
 * `{level, reason}` 对象（不是数值），旧实现按数值 `*100` 渲染出「置信度 NaN%」。
 */
function ConfidenceBadge({ confidence }: { confidence?: unknown }) {
  const level = confidenceLevel(confidence);
  const meta = level === 'high'
    ? { label: '高置信', cls: 'border-primary/40 bg-accent text-accent-foreground' }
    : level === 'medium'
      ? { label: '中置信', cls: 'border-gold/60 bg-gold-muted text-gold' }
      : level === 'low'
        ? { label: '低置信', cls: 'border-border bg-muted text-muted-foreground' }
        : { label: '置信度未知', cls: 'border-border bg-muted text-muted-foreground' };
  return (
    <Badge variant="outline" className={meta.cls}>
      {meta.label}
    </Badge>
  );
}

export default function Home() {
  const [favorites, setFavorites] = useState<Favorite[]>([]);
  const [records, setRecords] = useState<ExperimentRecord[]>([]);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [online, setOnline] = useState<boolean | null>(null); // null=检测中
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // 先探测后端健康状态（静默，不弹 toast）
        await healthApi.check();
        if (cancelled) return;
        setOnline(true);
        // 并行拉取仪表盘数据
        const [fav, rec, pln, sug] = await Promise.all([
          favoritesApi.list(),
          recordsApi.list(),
          iterateApi.listPlans(),
          iterateApi.listSuggestions(),
        ]);
        if (cancelled) return;
        setFavorites(Array.isArray(fav) ? fav : []);
        setRecords(Array.isArray(rec) ? rec : []);
        setPlans(Array.isArray(pln) ? pln : []);
        setSuggestions(Array.isArray(sug) ? sug : []);
      } catch (e) {
        if (!cancelled && e instanceof BackendUnavailableError) setOnline(false);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // 最近 5 条实验记录（按实验时间倒序；v1.9.3：用 experiment_date 派生字段）
  const recentRecords = useMemo(
    () => [...records]
      .sort((a, b) => String(experimentTime(b).date).localeCompare(String(experimentTime(a).date)))
      .slice(0, 5),
    [records],
  );

  // 最新批次的建议（取 batch 最大的 2 条，按创建时间倒序）
  const latestSuggestions = useMemo(() => {
    if (suggestions.length === 0) return [];
    const latestBatch = suggestions.reduce(
      (max, s) => (String(s.batch) > String(max) ? s.batch : max),
      suggestions[0].batch,
    );
    return suggestions
      .filter((s) => s.batch === latestBatch)
      .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
      .slice(0, 2);
  }, [suggestions]);

  // 统计卡配置（v1.9.4 第 3 批：仪表化——真实趋势序列 + 环比，无数据则不画图不造数）
  const weekly = useMemo(() => weeklyCounts(records, 8), [records]);
  const weeklyDelta = (weekly[weekly.length - 1] ?? 0) - (weekly[weekly.length - 2] ?? 0);
  const batchBars = useMemo(() => {
    const byBatch = new Map<string, number>();
    suggestions.forEach((s) => {
      const k = String(s.batch ?? '');
      if (k) byBatch.set(k, (byBatch.get(k) ?? 0) + 1);
    });
    return [...byBatch.entries()].slice(-6);
  }, [suggestions]);

  const stats: KpiCardProps[] = [
    {
      label: '收藏数', value: favorites.length, icon: Star, to: '/mine',
      unit: '条', hint: '点击查看收藏夹',
    },
    {
      label: '实验记录数', value: records.length, icon: FlaskConical, to: '/records',
      unit: '条', series: weekly, trend: { text: '近 8 周', delta: weeklyDelta },
    },
    {
      label: '方案数', value: plans.length, icon: ClipboardList, to: '/iterate',
      unit: '个', hint: 'GraphRAG 方案迭代',
    },
    {
      label: '建议数', value: suggestions.length, icon: Lightbulb, to: '/iterate',
      unit: '条',
      bars: batchBars.length > 0
        ? { values: batchBars.map(([, v]) => v), labels: batchBars.map(([k]) => `批次 ${k}`) }
        : undefined,
      hint: batchBars.length === 0 ? '暂无批次数据' : undefined,
    },
  ];

  return (
    <div className="space-y-7">
      {/* 页头：标题 + 后端状态 */}
      <PageHeader
        title="COF 科研系统"
        subtitle="机器学习辅助的 COF 成膜条件推荐与实验管理"
        accent
        actions={(
          <div className="flex items-center gap-2 rounded-full border border-border/70 bg-card px-3 py-1.5 text-xs">
            <span
              className={
                online === null
                  ? 'h-2 w-2 animate-pulse rounded-full bg-muted-foreground'
                  : online
                    ? 'h-2 w-2 rounded-full bg-success'
                    : 'h-2 w-2 rounded-full bg-muted-foreground'
              }
            />
            <span className="text-muted-foreground">
              {online === null ? '检测中…' : online ? '后端已连接' : '后端未连接'}
            </span>
          </div>
        )}
      />

      {/* 后端未连接时的优雅降级提示 */}
      {online === false && (
        <div className="rounded-xl border border-dashed border-gold/50 bg-gold-muted/40 px-5 py-4 text-sm text-muted-foreground">
          后端未连接：请启动 FastAPI 服务（http://localhost:8000）后刷新页面。下方展示的是离线占位数据。
        </div>
      )}

      {/* 四张统计卡（仪表化：数值焦点 + 真实迷你趋势） */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {stats.map((s) => (
          <KpiCard key={s.label} {...s} loading={loading} />
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* 最近实验记录 */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-base">最近实验记录</CardTitle>
            <Link to="/records" className="flex items-center gap-1 text-xs text-primary hover:underline">
              全部 <ArrowRight className="h-3 w-3" />
            </Link>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="space-y-2 py-2">
                {[0, 1, 2].map((i) => <Skeleton key={i} className="h-6 w-full" />)}
              </div>
            ) : recentRecords.length === 0 ? (
              <Empty className="border-0 p-6">
                <EmptyHeader>
                  <EmptyMedia variant="icon"><FlaskConical /></EmptyMedia>
                  <EmptyTitle className="text-sm">暂无实验记录</EmptyTitle>
                  <EmptyDescription className="text-xs">
                    从「实验记录」新建，或在打分页保存方案后自动归档
                  </EmptyDescription>
                </EmptyHeader>
              </Empty>
            ) : (
              <ul className="stagger divide-y divide-border/70">
                {recentRecords.map((r) => (
                  <li key={r.record_id} className="flex items-center justify-between py-2.5 text-sm">
                    <span className="font-medium text-foreground">{r.experiment_no}</span>
                    <span className="num-data text-xs text-muted-foreground">
                      {experimentTime(r).date}
                      {experimentTime(r).isFallback && (
                        <span className="ml-1 text-muted-foreground/60">（录入）</span>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        {/* 最新批次建议预览 */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-base">最新建议</CardTitle>
            <Link to="/iterate" className="flex items-center gap-1 text-xs text-primary hover:underline">
              全部 <ArrowRight className="h-3 w-3" />
            </Link>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="space-y-3 py-2">
                {[0, 1].map((i) => <Skeleton key={i} className="h-14 w-full" />)}
              </div>
            ) : latestSuggestions.length === 0 ? (
              <Empty className="border-0 p-6">
                <EmptyHeader>
                  <EmptyMedia variant="icon"><Lightbulb /></EmptyMedia>
                  <EmptyTitle className="text-sm">暂无建议</EmptyTitle>
                  <EmptyDescription className="text-xs">
                    在「方案迭代」跑一次 GraphRAG，建议会自动出现在这里
                  </EmptyDescription>
                </EmptyHeader>
              </Empty>
            ) : (
              <ul className="stagger space-y-3">
                {latestSuggestions.map((s) => (
                  <li
                    key={s.suggestion_id}
                    className="flex items-center justify-between rounded-xl border border-border/70 bg-muted/30 px-4 py-3 transition-colors hover:bg-muted/50"
                  >
                    <div>
                      <div className="text-sm font-medium text-foreground">{s.payload?.title}</div>
                      <div className="mt-0.5 text-xs text-muted-foreground">
                        批次 {s.batch} · {s.status}
                      </div>
                    </div>
                    <ConfidenceBadge confidence={s.payload?.confidence} />
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
