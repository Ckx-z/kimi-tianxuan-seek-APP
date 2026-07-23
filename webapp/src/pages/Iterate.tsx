/**
 * 方案迭代页（页⑤）：提问生成迭代建议（慢请求异步体验）、建议列表按批次分组、
 * 采纳生成方案、已生成方案展示。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { Loader2, Sparkles } from 'lucide-react';
import type { ExperimentRecord, Favorite, Plan, Suggestion } from '@/types';
import {
  listFavorites,
  listPlans,
  listRecords,
  listSuggestions,
  suggestIterate,
} from '@/components/iterate/api';
import { SuggestionCard } from '@/components/iterate/SuggestionCard';
import { PlanCardItem } from '@/components/iterate/PlanCardItem';

/** 「全部记录（不锚定）」选项值（Select 不允许空串） */
const NO_ANCHOR = '__none__';

/** 实验记录标签：日期｜编号｜结果 */
function recordLabel(r: ExperimentRecord): string {
  const date = (r.date || '').slice(0, 10) || '未知日期';
  const outcome = typeof r.outcome === 'string' ? r.outcome : '';
  return `${date}｜${r.experiment_no || r.record_id}｜${outcome || '—'}`;
}

/** 收藏标签：醛 ＋ 胺（真实数据为对象，兼容字符串） */
function favoriteLabel(f: Favorite): string {
  const raw = f as unknown as {
    aldehyde?: { name?: string } | string;
    amine?: { name?: string } | string;
  };
  const ald = typeof raw.aldehyde === 'object' ? (raw.aldehyde?.name ?? '') : (raw.aldehyde ?? '');
  const amine = typeof raw.amine === 'object' ? (raw.amine?.name ?? '') : (raw.amine ?? '');
  const pair = [ald, amine].filter(Boolean).join(' ＋ ');
  return pair ? `${pair}（${f.id}）` : f.id;
}

export default function Iterate() {
  // 顶部提问区状态
  const [favorites, setFavorites] = useState<Favorite[]>([]);
  const [favId, setFavId] = useState<string>('');
  const [records, setRecords] = useState<ExperimentRecord[]>([]);
  const [recordId, setRecordId] = useState<string>(NO_ANCHOR);
  const [question, setQuestion] = useState('');
  const [generating, setGenerating] = useState(false);
  const [favError, setFavError] = useState(false);

  // 列表状态
  const [suggestions, setSuggestions] = useState<Suggestion[] | null>(null);
  const [plans, setPlans] = useState<Plan[] | null>(null);
  const [listError, setListError] = useState(false);

  // 慢请求 AbortController：路由切换/卸载时中止，不阻塞导航
  const suggestAbortRef = useRef<AbortController | null>(null);
  useEffect(() => () => suggestAbortRef.current?.abort(), []);

  // 初始加载收藏
  useEffect(() => {
    listFavorites()
      .then((list) => {
        setFavorites(list);
        if (list.length > 0) setFavId(list[0].id);
      })
      .catch(() => setFavError(true));
  }, []);

  // 收藏变化 → 级联加载实验记录
  useEffect(() => {
    setRecordId(NO_ANCHOR);
    setRecords([]);
    if (!favId) return;
    listRecords(favId).then(setRecords).catch(() => setRecords([]));
  }, [favId]);

  // 刷新建议与方案列表
  const refreshLists = useCallback(() => {
    setListError(false);
    listSuggestions().then(setSuggestions).catch(() => { setSuggestions([]); setListError(true); });
    listPlans().then(setPlans).catch(() => setPlans([]));
  }, []);
  useEffect(refreshLists, [refreshLists]);

  // 生成迭代建议（异步体验：转圈 + 提示，完成 toast + 刷新列表）
  const handleSuggest = async () => {
    const q = question.trim();
    if (!q) {
      toast.error('请先输入问题');
      return;
    }
    suggestAbortRef.current?.abort();
    const ctrl = new AbortController();
    suggestAbortRef.current = ctrl;
    setGenerating(true);
    try {
      const res = await suggestIterate(
        {
          question: q,
          favorite_id: favId || undefined,
          record_id: recordId === NO_ANCHOR ? undefined : recordId,
        },
        ctrl.signal,
      );
      toast.success(`已生成 ${res.count} 条迭代建议`);
      setQuestion('');
      refreshLists();
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return; // 页面已切换
      // 错误 toast 已在 api 层弹出；生成失败仍刷新一次（防止超时但后台写成功）
      refreshLists();
    } finally {
      if (suggestAbortRef.current === ctrl) setGenerating(false);
    }
  };

  // 建议按 batch 分组：有 batch 的按批次，缺 batch 归入「历史」
  const batches = useMemo(() => {
    const groups = new Map<string, Suggestion[]>();
    for (const s of suggestions ?? []) {
      const key = s.batch || 'legacy';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key)!.push(s);
    }
    // 组内按创建时间倒序取最新时间，用于组排序（最新批次在前）
    return [...groups.entries()]
      .map(([batch, items]) => ({
        batch,
        items,
        latest: Math.max(...items.map((s) => Date.parse(s.created_at) || 0)),
      }))
      .sort((a, b) => b.latest - a.latest);
  }, [suggestions]);

  const loadingLists = suggestions === null || plans === null;

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold text-foreground">方案迭代</h1>

      {/* ① 顶部提问区 */}
      <Card className="bg-card">
        <CardContent className="space-y-4 p-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label>选择收藏</Label>
              <Select value={favId} onValueChange={setFavId} disabled={favError || favorites.length === 0}>
                <SelectTrigger>
                  <SelectValue placeholder={favError ? '后端未连接，无法加载' : '选择单体对收藏'} />
                </SelectTrigger>
                <SelectContent>
                  {favorites.map((f) => (
                    <SelectItem key={f.id} value={f.id}>{favoriteLabel(f)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>锚定实验记录（可选）</Label>
              <Select value={recordId} onValueChange={setRecordId} disabled={!favId}>
                <SelectTrigger>
                  <SelectValue placeholder="全部记录（不锚定）" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NO_ANCHOR}>全部记录（不锚定）</SelectItem>
                  {records.map((r) => (
                    <SelectItem key={r.record_id} value={r.record_id}>{recordLabel(r)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label>你的问题</Label>
            <Textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="例如：这次失败了，下次怎么调条件？"
              rows={3}
              disabled={generating}
            />
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <Button
              onClick={handleSuggest}
              disabled={generating || !question.trim()}
              className="bg-primary text-primary-foreground"
            >
              {generating ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Sparkles className="mr-2 h-4 w-4" />
              )}
              生成迭代建议
            </Button>
            {generating && (
              <span className="text-sm text-gold">生成中，约 1-3 分钟，可切换页面</span>
            )}
          </div>
        </CardContent>
      </Card>

      {/* 列表加载骨架 */}
      {loadingLists && (
        <div className="space-y-3">
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      )}

      {/* 后端降级提示（不白屏） */}
      {!loadingLists && listError && (
        <div className="rounded-xl border border-dashed border-gold/50 bg-gold-muted p-6 text-center text-sm text-gold-foreground">
          后端未连接，建议列表暂不可用。请确认 FastAPI 服务已启动（http://localhost:8000）后刷新页面。
        </div>
      )}

      {/* ② 建议列表（按批次分组） */}
      {!loadingLists && !listError && (
        <section className="space-y-4">
          <h2 className="text-lg font-semibold text-foreground">迭代建议</h2>
          {batches.length === 0 ? (
            /* ⑤ 空态引导 */
            <div className="rounded-xl border border-dashed border-border bg-card p-12 text-center text-muted-foreground">
              还没有迭代建议。在上方选择一个收藏，描述你的实验问题（如「这次失败了，下次怎么调条件？」），点击「生成迭代建议」开始。
            </div>
          ) : (
            batches.map(({ batch, items }, idx) => {
              const isLatest = idx === 0;
              return (
                <div
                  key={batch}
                  className={
                    isLatest
                      ? 'space-y-3 rounded-xl border-2 border-primary/60 bg-gold-muted/40 p-4'
                      : 'space-y-3'
                  }
                >
                  <h3
                    className={
                      isLatest
                        ? 'font-medium text-primary'
                        : 'text-sm text-muted-foreground'
                    }
                  >
                    {isLatest ? '✨ 本次新建议' : `历史批次 ${batch === 'legacy' ? '' : batch}`}
                  </h3>
                  <div className="space-y-3">
                    {items.map((s) => (
                      <SuggestionCard key={s.suggestion_id} suggestion={s} onAdopted={refreshLists} />
                    ))}
                  </div>
                </div>
              );
            })
          )}
        </section>
      )}

      {/* ④ 生成的方案展示区 */}
      {!loadingLists && (
        <section className="space-y-4">
          <h2 className="text-lg font-semibold text-foreground">已生成方案</h2>
          {(plans ?? []).length === 0 ? (
            <div className="rounded-xl border border-dashed border-border bg-card p-10 text-center text-muted-foreground">
              暂无方案。采纳上方建议后，会自动生成编号的实验方案卡。
            </div>
          ) : (
            <div className="grid gap-4 lg:grid-cols-2">
              {(plans ?? []).map((p) => (
                <PlanCardItem key={p.plan_id} plan={p} />
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
