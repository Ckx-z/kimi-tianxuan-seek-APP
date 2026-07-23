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
import { favoritesApi, healthApi, iterateApi, recordsApi, BackendUnavailableError } from '@/lib/api';
import type { ExperimentRecord, Favorite, Plan, Suggestion } from '@/types';

/** 置信度徽章（金色系，按置信度分档） */
function ConfidenceBadge({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const variant = value >= 0.8 ? 'high' : value >= 0.5 ? 'mid' : 'low';
  const cls =
    variant === 'high'
      ? 'border-gold/60 bg-gold-muted text-gold-foreground'
      : variant === 'mid'
        ? 'border-primary/40 bg-accent text-accent-foreground'
        : 'border-border bg-muted text-muted-foreground';
  return (
    <Badge variant="outline" className={cls}>
      置信度 {pct}%
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

  // 最近 5 条实验记录（按日期倒序）
  const recentRecords = useMemo(
    () => [...records].sort((a, b) => String(b.date).localeCompare(String(a.date))).slice(0, 5),
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

  // 统计卡配置
  const stats = [
    { label: '收藏数', value: favorites.length, icon: Star, to: '/mine' },
    { label: '实验记录数', value: records.length, icon: FlaskConical, to: '/records' },
    { label: '方案数', value: plans.length, icon: ClipboardList, to: '/iterate' },
    { label: '建议数', value: suggestions.length, icon: Lightbulb, to: '/iterate' },
  ];

  return (
    <div className="space-y-8">
      {/* 页头：标题 + 后端状态 */}
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gradient-royal">COF 成膜推荐系统</h1>
          <p className="mt-1 text-sm text-muted-foreground">机器学习辅助的 COF 成膜条件推荐与实验管理</p>
        </div>
        {/* 后端健康状态指示 */}
        <div className="flex items-center gap-2 rounded-full border border-border bg-card px-3 py-1.5 text-xs">
          <span
            className={
              online === null
                ? 'h-2 w-2 animate-pulse rounded-full bg-muted-foreground'
                : online
                  ? 'h-2 w-2 rounded-full bg-emerald-500'
                  : 'h-2 w-2 rounded-full bg-gray-400'
            }
          />
          <span className="text-muted-foreground">
            {online === null ? '检测中…' : online ? '后端已连接' : '后端未连接'}
          </span>
        </div>
      </div>

      {/* 后端未连接时的优雅降级提示 */}
      {online === false && (
        <div className="rounded-xl border border-dashed border-gold/50 bg-gold-muted/40 px-5 py-4 text-sm text-muted-foreground">
          后端未连接：请启动 FastAPI 服务（http://localhost:8000）后刷新页面。下方展示的是离线占位数据。
        </div>
      )}

      {/* 四张统计卡 */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {stats.map(({ label, value, icon: Icon, to }) => (
          <Link key={label} to={to}>
            <Card className="transition-shadow hover:shadow-md hover:shadow-primary/10">
              <CardHeader className="flex flex-row items-center justify-between pb-2">
                <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
                <Icon className="h-4 w-4 text-gold" />
              </CardHeader>
              <CardContent>
                <div className="text-3xl font-semibold text-primary">
                  {loading ? '—' : value}
                </div>
              </CardContent>
            </Card>
          </Link>
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
            {recentRecords.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">暂无记录</p>
            ) : (
              <ul className="divide-y divide-border">
                {recentRecords.map((r) => (
                  <li key={r.record_id} className="flex items-center justify-between py-2.5 text-sm">
                    <span className="font-medium text-foreground">{r.experiment_no}</span>
                    <span className="text-xs text-muted-foreground">{r.date}</span>
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
            {latestSuggestions.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">暂无建议</p>
            ) : (
              <ul className="space-y-3">
                {latestSuggestions.map((s) => (
                  <li
                    key={s.suggestion_id}
                    className="flex items-center justify-between rounded-lg border border-border bg-muted/40 px-4 py-3"
                  >
                    <div>
                      <div className="text-sm font-medium text-foreground">{s.payload?.title}</div>
                      <div className="mt-0.5 text-xs text-muted-foreground">
                        批次 {s.batch} · {s.status}
                      </div>
                    </div>
                    <ConfidenceBadge value={s.payload?.confidence ?? 0} />
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
