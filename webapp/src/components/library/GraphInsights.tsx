/**
 * 引用图分析 + 图查询（v1.9.8 C2 / C4 的界面）
 *
 * 左块「引用图分析」：PageRank Top / 社区 / 桥梁文献 / 时间线（一次请求 `/analytics?kind=all`）
 * 右块「图查询」：受限只读 DSL 输入框 + 自然语言开关（`nl=true` 时由后端翻译，且仅允许白名单算子）
 *
 * 设计取舍：结果按"通用但可读"的方式呈现 —— 分析给排行榜，查询给结构化摘要 + 前若干条明细，
 * 不追求图表化（离线桌面场景，先把能力用起来）。
 */
import { useCallback, useEffect, useState } from 'react';
import { BarChart3, Loader2, Search, Sparkles, TrendingUp } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';

interface RankRow {
  paper_id: string; title?: string; value?: number; score?: number;
  year?: number | null; size?: number;
}

const DSL_EXAMPLES = [
  'neighbors 1529 depth=2 pred=cites',
  'path 1529 1781 max=4',
  'by_entity technique PXRD limit=20',
  'by_metric 比表面积 min=2000',
  'cites_of 1529 dir=in limit=30',
  'top pagerank limit=10',
];

export function GraphInsights() {
  const [analytics, setAnalytics] = useState<Record<string, unknown> | null>(null);
  const [loadingA, setLoadingA] = useState(false);
  const [dsl, setDsl] = useState('top pagerank limit=5');
  const [useNl, setUseNl] = useState(false);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [loadingQ, setLoadingQ] = useState(false);

  const loadAnalytics = useCallback(async (force = false) => {
    setLoadingA(true);
    try {
      const r = await fetch(`/api/library/analytics?kind=all${force ? '&force=true' : ''}`);
      const d = r.ok ? await r.json() : {};
      setAnalytics(d);
    } catch {
      toast.error('分析加载失败');
    } finally {
      setLoadingA(false);
    }
  }, []);

  useEffect(() => { void loadAnalytics(false); }, [loadAnalytics]);

  // G0：图谱构建完成后自动刷新（由 LibraryExplorer 的「构建图谱」广播）
  useEffect(() => {
    const onUpdated = () => { void loadAnalytics(true); };
    window.addEventListener('cof:graph-updated', onUpdated);
    return () => window.removeEventListener('cof:graph-updated', onUpdated);
  }, [loadAnalytics]);

  const runQuery = async () => {
    if (!dsl.trim()) return;
    setLoadingQ(true);
    setResult(null);
    try {
      const r = await fetch(`/api/library/graph/query?q=${encodeURIComponent(dsl)}`
                            + `&nl=${useNl ? 'true' : 'false'}`);
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || `查询失败（${r.status}）`);
      setResult(d);
      if (d.dsl && useNl) toast.info(`已翻译为：${d.dsl}`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '查询失败');
    } finally {
      setLoadingQ(false);
    }
  };

  const pr = (analytics?.pagerank ?? {}) as { results?: RankRow[]; nodes?: number };
  const cm = (analytics?.communities ?? {}) as { count?: number; communities?: {
    community_id: number; size: number; papers: RankRow[] }[] };
  const br = (analytics?.bridges ?? {}) as { results?: RankRow[] };
  const tl = (analytics?.timeline ?? {}) as { years?: number; total?: number;
    buckets?: { year: number; count: number }[] };

  const renderList = (rows: RankRow[] | undefined, unit = '') => (
    rows && rows.length > 0 ? (
      <ul className="space-y-0.5">
        {rows.map((r, i) => (
          <li key={`${r.paper_id}-${i}`} className="flex items-baseline gap-1 text-[11px]">
            <span className="text-muted-foreground">{i + 1}.</span>
            <span className="min-w-0 flex-1 truncate" title={r.title || r.paper_id}>
              #{r.paper_id} {r.title || ''}
            </span>
            <span className="shrink-0 text-muted-foreground">
              {typeof r.value === 'number' ? r.value.toFixed(4)
                : typeof r.score === 'number' ? r.score.toFixed(4) : ''}{unit}
            </span>
          </li>
        ))}
      </ul>
    ) : <p className="text-[11px] text-muted-foreground">（无数据：先执行图谱同步）</p>
  );

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      {/* C2：引用图分析 */}
      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-1 text-sm">
              <BarChart3 className="h-3.5 w-3.5" />引用图分析
            </CardTitle>
            <div className="flex items-center gap-1">
              {(pr.nodes ?? 0) > 0 && (
                <span className="text-[10px] text-muted-foreground">图内文献 {pr.nodes}</span>
              )}
              <Button size="sm" variant="outline" className="h-6 px-2 text-[11px]"
                      disabled={loadingA} onClick={() => void loadAnalytics(true)}>
                {loadingA ? <Loader2 className="h-3 w-3 animate-spin" /> : '重算'}
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-2.5">
          <div>
            <p className="mb-0.5 flex items-center gap-1 text-[11px] font-medium">
              <TrendingUp className="h-3 w-3" />被引权重 Top（PageRank，奖励被引）
            </p>
            {renderList(pr.results?.slice(0, 5))}
          </div>
          <div>
            <p className="mb-0.5 text-[11px] font-medium">
              研究簇（社区发现）· 共 {cm.count ?? 0} 个
            </p>
            {cm.communities && cm.communities.length > 0 ? (
              <ul className="space-y-0.5">
                {cm.communities.slice(0, 3).map((c) => (
                  <li key={c.community_id} className="text-[11px]">
                    <span className="text-muted-foreground">簇{c.community_id + 1}</span>
                    <span className="ml-1">({c.size} 篇)</span>
                    <span className="ml-1 text-muted-foreground">
                      {c.papers.slice(0, 3).map((p) => `#${p.paper_id}`).join(' / ')}
                    </span>
                  </li>
                ))}
              </ul>
            ) : <p className="text-[11px] text-muted-foreground">（无数据）</p>}
          </div>
          <div>
            <p className="mb-0.5 text-[11px] font-medium">桥梁文献（介数中心性）</p>
            {renderList(br.results?.slice(0, 5))}
          </div>
          <div>
            <p className="mb-0.5 text-[11px] font-medium">
              时间线 · {tl.years ?? 0} 个年份 / {tl.total ?? 0} 篇有年份
            </p>
            {tl.buckets && tl.buckets.length > 0 ? (
              <p className="text-[11px] text-muted-foreground">
                {tl.buckets.slice(-8).map((b) => `${b.year}:${b.count}`).join('  ')}
              </p>
            ) : (
              <p className="text-[11px] text-muted-foreground">
                （年份缺失较多 → 可用上方「补全元数据」从 OpenAlex 补齐）
              </p>
            )}
          </div>
        </CardContent>
      </Card>

      {/* C4：图查询 */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-1 text-sm">
            <Search className="h-3.5 w-3.5" />图查询（只读 DSL）
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <div className="flex items-center gap-1">
            <Input value={dsl} onChange={(e) => setDsl(e.target.value)}
                   onKeyDown={(e) => { if (e.key === 'Enter') void runQuery(); }}
                   placeholder="例如：neighbors 1529 depth=2 pred=cites"
                   className="h-7 font-mono text-[11px]" />
            <label className="flex shrink-0 items-center gap-1 text-[11px] text-muted-foreground">
              <input type="checkbox" checked={useNl}
                     onChange={(e) => setUseNl(e.target.checked)} />
              自然语言
            </label>
            <Button size="sm" className="h-7 px-2 text-[11px]" disabled={loadingQ}
                    onClick={() => void runQuery()}>
              {loadingQ ? <Loader2 className="h-3 w-3 animate-spin" />
                : <Sparkles className="h-3 w-3" />}
            </Button>
          </div>
          <p className="text-[10px] text-muted-foreground">
            可用算子：neighbors / path / by_entity / by_metric / cites_of / top ——
            **只读**，写操作会被拒绝；勾选「自然语言」后由模型翻译成上述算子（非法输出会被拒绝）。
          </p>
          <div className="flex flex-wrap gap-1">
            {DSL_EXAMPLES.map((ex) => (
              <button key={ex} type="button" onClick={() => setDsl(ex)}
                      className="rounded-full border border-border px-1.5 py-0.5 text-[10px]
                                 text-muted-foreground hover:bg-accent">
                {ex.split(' ')[0]}
              </button>
            ))}
          </div>
          {result && (
            <div className="max-h-64 overflow-auto rounded-md border border-border/70 bg-muted/20 p-2">
              <p className="mb-1 text-[10px] text-muted-foreground">
                op={String(result.op)} · {typeof result.count === 'number'
                  ? `命中 ${result.count}` : ''}
                {typeof result.length === 'number' ? ` · 路径长度 ${result.length}` : ''}
                {result.found === false ? ' · 未找到路径' : ''}
              </p>
              {Array.isArray(result.papers as unknown) && (
                <ul className="space-y-0.5">
                  {(result.papers as RankRow[]).map((p) => (
                    <li key={p.paper_id} className="text-[11px]">#{p.paper_id} {p.title}</li>
                  ))}
                </ul>
              )}
              {Array.isArray(result.path as unknown) && (
                <p className="text-[11px]">{(result.path as string[]).join(' → ')}</p>
              )}
              {Array.isArray(result.results as unknown) && (
                <ul className="space-y-0.5">
                  {(result.results as RankRow[]).map((p, i) => (
                    <li key={`${p.paper_id}-${i}`} className="text-[11px]">
                      #{p.paper_id} {p.title || ''}
                      {typeof p.value === 'number' ? ` · ${p.value.toFixed(4)}` : ''}
                    </li>
                  ))}
                </ul>
              )}
              {Array.isArray(result.relations as unknown) && (
                <ul className="space-y-0.5">
                  {(result.relations as { direction: string; target: string }[])
                    .slice(0, 20).map((r, i) => (
                    <li key={i} className="text-[11px]">
                      {r.direction === 'out' ? '引用 →' : '← 被引'} {r.target}
                    </li>
                  ))}
                </ul>
              )}
              {Array.isArray(result.metrics as unknown) && (
                <ul className="space-y-0.5">
                  {(result.metrics as { name: string; value: number; unit: string;
                    paper_id: string }[]).map((m, i) => (
                    <li key={i} className="text-[11px]">
                      #{m.paper_id} {m.name} = {m.value} {m.unit}
                    </li>
                  ))}
                </ul>
              )}
              {typeof result.nodes === 'object' && result.nodes !== null
                && !Array.isArray(result.nodes) && (
                <ul className="space-y-0.5">
                  {Object.keys(result.nodes as Record<string, unknown>)
                    .slice(0, 20).map((k) => (
                    <li key={k} className="text-[11px]">
                      #{k} {(result.nodes as Record<string, { title?: string }>)[k]?.title || ''}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

export default GraphInsights;
