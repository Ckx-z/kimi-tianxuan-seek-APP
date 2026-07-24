/**
 * 「我的收藏」卡片墙
 * - 卡片：醛名 × 胺名大字 + SMILES 小字 + 最新预测分徽章 + 创建时间 + 删除按钮
 * - 点击卡片弹出详情 Dialog：单体信息 / 预测快照 / 文献列表 / 该收藏的实验记录简表
 * - 删除经 AlertDialog 确认后调用 DELETE /api/favorites/{id}
 */
import { useEffect, useState, type Dispatch, type SetStateAction } from 'react';
import { Trash2, BookOpen, FlaskConical } from 'lucide-react';
import { toast } from 'sonner';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  deleteFavorite,
  fetchRecordsByFavorite,
  type FavoriteItem,
  type PredictionSnapshot,
  type RecordItem,
  type ReferenceItem,
} from './api';
// 只读复用查询打分页的结果组件与 API（不修改其文件），保证放大详情与查询打分页内容一致
import ResultCard from '@/components/query/ResultCard';
import MonomerPropsCard from '@/components/query/MonomerPropsCard';
import PlanCardPanel from '@/components/query/PlanCardPanel';
import {
  fetchMonomerProps,
  fetchPlanCard,
  fetchPlanTemplates,
  type MonomerProps,
  type PlanCardData,
  type PlanTemplateItem,
  type PredictResult,
} from '@/components/query/api';

/** 截断 SMILES 显示 */
function shortSmiles(s?: string, n = 42): string {
  if (!s) return '—';
  return s.length > n ? `${s.slice(0, n)}…` : s;
}

/** 预测分徽章（金色系） */
function ScoreBadge({ fav }: { fav: FavoriteItem }) {
  const score = fav.latest_prediction?.score;
  if (typeof score !== 'number') {
    return (
      <Badge variant="outline" className="border-border bg-muted text-muted-foreground">
        未打分
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="border-gold/60 bg-gold-muted text-gold-foreground">
      {score.toFixed(2)} 分
    </Badge>
  );
}

/** 匹配类型中文标签 */
function matchLabel(t?: string): string {
  return t === 'both' ? '醛胺同报道' : t === 'aldehyde' ? '报道过该醛' : t === 'amine' ? '报道过该胺' : '相关';
}

/** 文献列表 */
function ReferenceList({ refs }: { refs?: ReferenceItem[] }) {
  if (!refs || refs.length === 0) {
    return <p className="text-sm text-muted-foreground">暂无关联文献</p>;
  }
  return (
    <ul className="space-y-2">
      {refs.map((r, i) => (
        <li
          key={`${r.title}-${i}`}
          className="flex items-start justify-between gap-3 rounded-lg border border-border bg-muted/40 px-3 py-2"
        >
          <div className="min-w-0">
            <div className="truncate text-sm font-medium text-foreground">{r.title || '未命名文献'}</div>
            <div className="mt-0.5 text-xs text-muted-foreground">
              {r.note || matchLabel(r.match_type)}
              {typeof r.count === 'number' ? ` · 出现 ${r.count} 次` : ''}
              {r.doi ? ` · DOI: ${r.doi}` : ''}
            </div>
          </div>
          <Badge variant="outline" className="shrink-0 border-primary/40 text-primary">
            {matchLabel(r.match_type)}
          </Badge>
        </li>
      ))}
    </ul>
  );
}

/** 单体 2D 结构图（/api/monomers/structure.svg，失败时静默隐藏） */
function StructureImg({ smiles, label }: { smiles?: string; label: string }) {
  const [failed, setFailed] = useState(false);
  if (!smiles || failed) return null;
  return (
    <img
      src={`/api/monomers/structure.svg?smiles=${encodeURIComponent(smiles)}`}
      alt={`${label}结构图`}
      onError={() => setFailed(true)}
      className="mt-2 h-28 w-full rounded-md border border-border bg-white object-contain dark:bg-white/95"
    />
  );
}

/** 收藏快照 → 查询打分页 ResultCard 的 PredictResult（口径对齐） */
function snapshotToResult(p?: PredictionSnapshot | null): PredictResult | null {
  if (!p) return null;
  const oodRaw = p.ood;
  const ood =
    typeof oodRaw === 'string'
      ? { level: oodRaw || 'none', reasons: [] as string[] }
      : { level: oodRaw?.level ?? 'none', reasons: oodRaw?.reasons ?? [] };
  const hasScore = typeof p.score === 'number';
  const hasSub = typeof p.tree_score === 'number' || typeof p.gnn_score === 'number';
  if (!hasScore && !hasSub && ood.level !== 'out') return null;
  const num = (v: unknown): number | null => (typeof v === 'number' ? v : null);
  return {
    score: num(p.score),
    score_policy: p.score_policy ?? 'max_tree_gnn',
    tree_score: num(p.tree_score),
    tree_std: num(p.tree_std) ?? num(p.std),
    tree_model_name: p.tree_model_name ?? p.arm ?? null,
    tree_route: p.tree_route ?? null,
    gnn_score: num(p.gnn_score),
    gnn_std: num(p.gnn_std),
    ood,
  };
}

/** 单侧性质卡状态 */
interface PropsState {
  loading: boolean;
  error: string | null;
  data: MonomerProps | null;
}
const emptyProps: PropsState = { loading: false, error: null, data: null };

/** 收藏详情 Dialog（与查询打分页结果内容一致：分数/OOD/结构图/性质卡/方案卡） */
function FavoriteDetailDialog({
  fav,
  open,
  onOpenChange,
}: {
  fav: FavoriteItem | null;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const [records, setRecords] = useState<RecordItem[]>([]);
  const [recLoading, setRecLoading] = useState(false);
  const [aldProps, setAldProps] = useState<PropsState>(emptyProps);
  const [amineProps, setAmineProps] = useState<PropsState>(emptyProps);
  const [planCard, setPlanCard] = useState<PlanCardData | null>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const [templates, setTemplates] = useState<PlanTemplateItem[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(false);
  const [templateId, setTemplateId] = useState('');

  // 打开时拉取：实验记录 + 性质卡×2 + 方案卡模板/方案卡（与查询页联动口径一致）
  useEffect(() => {
    if (!open || !fav) return;
    let cancelled = false;
    setRecLoading(true);
    fetchRecordsByFavorite(fav.id)
      .then((list) => !cancelled && setRecords(list))
      .catch(() => !cancelled && setRecords([]))
      .finally(() => !cancelled && setRecLoading(false));

    const loadProps = (
      smiles: string | undefined,
      name: string | undefined,
      setter: Dispatch<SetStateAction<PropsState>>,
    ) => {
      if (!smiles) {
        setter(emptyProps);
        return;
      }
      setter({ loading: true, error: null, data: null });
      fetchMonomerProps(smiles, name ?? '')
        .then((data) => !cancelled && setter({ loading: false, error: null, data }))
        .catch((e) =>
          !cancelled &&
          setter({ loading: false, error: e instanceof Error ? e.message : '未知错误', data: null }),
        );
    };
    loadProps(fav.aldehyde?.smiles, fav.aldehyde?.name, setAldProps);
    loadProps(fav.amine?.smiles, fav.amine?.name, setAmineProps);

    setTemplatesLoading(true);
    fetchPlanTemplates()
      .then((list) => !cancelled && setTemplates(list))
      .catch(() => !cancelled && setTemplates([]))
      .finally(() => !cancelled && setTemplatesLoading(false));

    setTemplateId('');
    if (fav.aldehyde?.smiles && fav.amine?.smiles) {
      setPlanLoading(true);
      setPlanError(null);
      setPlanCard(null);
      fetchPlanCard({
        aldehyde_smiles: fav.aldehyde.smiles,
        amine_smiles: fav.amine.smiles,
        ald_name: fav.aldehyde.name ?? '',
        amine_name: fav.amine.name ?? '',
        template_id: null,
      })
        .then((card) => !cancelled && setPlanCard(card))
        .catch((e) => !cancelled && setPlanError(e instanceof Error ? e.message : '未知错误'))
        .finally(() => !cancelled && setPlanLoading(false));
    }
    return () => {
      cancelled = true;
    };
  }, [open, fav]);

  /** 切换模板 → 重新生成方案卡（与查询页一致） */
  const handleTemplateChange = (id: string) => {
    setTemplateId(id);
    if (!fav?.aldehyde?.smiles || !fav?.amine?.smiles) return;
    setPlanLoading(true);
    setPlanError(null);
    fetchPlanCard({
      aldehyde_smiles: fav.aldehyde.smiles,
      amine_smiles: fav.amine.smiles,
      ald_name: fav.aldehyde.name ?? '',
      amine_name: fav.amine.name ?? '',
      template_id: id || null,
    })
      .then((card) => setPlanCard(card))
      .catch((e) => setPlanError(e instanceof Error ? e.message : '未知错误'))
      .finally(() => setPlanLoading(false));
  };

  const handleTemplateUploaded = (tpl: PlanTemplateItem) => {
    setTemplates((prev) => [...prev.filter((t) => t.id !== tpl.id), tpl]);
    handleTemplateChange(tpl.id);
  };

  if (!fav) return null;
  const result = snapshotToResult(fav.latest_prediction);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-4xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="text-gradient-royal">
            {fav.aldehyde?.name || '未知醛'} × {fav.amine?.name || '未知胺'}
          </DialogTitle>
          <DialogDescription>
            收藏编号 {fav.id} · 创建于 {fav.created_at || '未知时间'}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5">
          {/* 单体信息（含 2D 结构图） */}
          <section>
            <h3 className="mb-2 text-sm font-semibold text-foreground">单体信息</h3>
            <div className="grid gap-3 sm:grid-cols-2">
              {(
                [
                  ['醛单体', fav.aldehyde],
                  ['胺单体', fav.amine],
                ] as const
              ).map(([label, m]) => (
                <div key={label} className="rounded-lg border border-border bg-muted/40 p-3 text-sm">
                  <div className="text-xs text-muted-foreground">{label}</div>
                  <div className="mt-1 font-medium text-foreground">{m?.name || '—'}</div>
                  <div className="mt-1 break-all font-mono text-xs text-muted-foreground">
                    {m?.smiles || '—'}
                  </div>
                  {m?.cas && <div className="mt-1 text-xs text-muted-foreground">CAS: {m.cas}</div>}
                  <StructureImg smiles={m?.smiles} label={label} />
                </div>
              ))}
            </div>
            {fav.notes && (
              <p className="mt-2 rounded-lg bg-gold-muted/40 px-3 py-2 text-xs text-muted-foreground">
                备注：{fav.notes}
              </p>
            )}
          </section>

          {/* 打分结果（复用查询页 ResultCard：主分数 + 树/GNN 分量 + OOD 横幅） */}
          <section>
            {result ? (
              <ResultCard result={result} loading={false} />
            ) : (
              <div className="rounded-xl border border-dashed border-border bg-card p-8 text-center text-sm text-muted-foreground">
                尚未打分，可在查询页对该组合进行预测；打分后此处显示与查询打分页一致的完整结果。
              </div>
            )}
          </section>

          {/* 单体性质卡（复用查询页 MonomerPropsCard：RDKit facts + LLM 解读） */}
          <section>
            <h3 className="mb-2 text-sm font-semibold text-foreground">单体性质</h3>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <MonomerPropsCard
                title="醛单体性质"
                name={fav.aldehyde?.name || undefined}
                loading={aldProps.loading}
                error={aldProps.error}
                props={aldProps.data}
              />
              <MonomerPropsCard
                title="胺单体性质"
                name={fav.amine?.name || undefined}
                loading={amineProps.loading}
                error={amineProps.error}
                props={amineProps.data}
              />
            </div>
          </section>

          {/* 方案卡（复用查询页 PlanCardPanel，可切换模板） */}
          <section>
            <PlanCardPanel
              card={planCard}
              loading={planLoading}
              error={planError}
              templates={templates}
              templatesLoading={templatesLoading}
              templateId={templateId}
              onTemplateChange={handleTemplateChange}
              onTemplateUploaded={handleTemplateUploaded}
              disabled={!fav.aldehyde?.smiles || !fav.amine?.smiles}
            />
          </section>

          {/* 文献列表 */}
          <section>
            <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-foreground">
              <BookOpen className="h-4 w-4 text-gold" /> 参考文献
            </h3>
            <ReferenceList refs={fav.references} />
          </section>

          {/* 实验记录简表 */}
          <section>
            <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-foreground">
              <FlaskConical className="h-4 w-4 text-gold" /> 实验记录
            </h3>
            {recLoading ? (
              <div className="space-y-2">
                <Skeleton className="h-8 w-full" />
                <Skeleton className="h-8 w-full" />
              </div>
            ) : records.length === 0 ? (
              <p className="text-sm text-muted-foreground">该收藏下暂无实验记录</p>
            ) : (
              <div className="rounded-lg border border-border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>编号</TableHead>
                      <TableHead>日期</TableHead>
                      <TableHead>成膜强度</TableHead>
                      <TableHead>操作人</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {records.map((r) => (
                      <TableRow key={r.record_id}>
                        <TableCell className="font-medium">{r.experiment_no || r.record_id}</TableCell>
                        <TableCell>{r.date || '—'}</TableCell>
                        <TableCell>{r.strength || '—'}</TableCell>
                        <TableCell>{r.operator || '—'}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </section>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** 收藏卡片墙主组件 */
export function FavoritesSection({
  favorites,
  loading,
  onChanged,
}: {
  favorites: FavoriteItem[];
  loading: boolean;
  /** 删除成功后通知父组件刷新 */
  onChanged: () => void;
}) {
  const [detail, setDetail] = useState<FavoriteItem | null>(null);
  const [toDelete, setToDelete] = useState<FavoriteItem | null>(null);
  const [deleting, setDeleting] = useState(false);

  async function confirmDelete() {
    if (!toDelete) return;
    setDeleting(true);
    try {
      await deleteFavorite(toDelete.id);
      toast.success(`已删除收藏 ${toDelete.id}`);
      setToDelete(null);
      onChanged();
    } catch {
      /* 错误已由 api 层 toast */
    } finally {
      setDeleting(false);
    }
  }

  if (loading) {
    return (
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-36 w-full rounded-xl" />
        ))}
      </div>
    );
  }

  if (favorites.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border bg-card p-12 text-center text-sm text-muted-foreground">
        暂无收藏：在查询页预测后可点击「收藏」将组合加入这里。
      </div>
    );
  }

  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {favorites.map((fav) => (
          <Card
            key={fav.id}
            className="cursor-pointer transition-shadow hover:shadow-md hover:shadow-primary/10"
            onClick={() => setDetail(fav)}
          >
            <CardContent className="space-y-2 p-4">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 text-base font-semibold leading-snug text-foreground">
                  {fav.aldehyde?.name || '未知醛'}
                  <span className="mx-1 text-gold">×</span>
                  {fav.amine?.name || '未知胺'}
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 shrink-0 text-muted-foreground hover:text-destructive"
                  title="删除收藏"
                  onClick={(e) => {
                    e.stopPropagation(); // 阻止触发卡片点击
                    setToDelete(fav);
                  }}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
              <div className="break-all font-mono text-xs text-muted-foreground">
                {shortSmiles(fav.aldehyde?.smiles)}
              </div>
              <div className="break-all font-mono text-xs text-muted-foreground">
                {shortSmiles(fav.amine?.smiles)}
              </div>
              <div className="flex items-center justify-between pt-1">
                <ScoreBadge fav={fav} />
                <span className="text-xs text-muted-foreground">{fav.created_at || ''}</span>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* 详情 Dialog */}
      <FavoriteDetailDialog
        fav={detail}
        open={detail !== null}
        onOpenChange={(v) => !v && setDetail(null)}
      />

      {/* 删除确认 */}
      <AlertDialog open={toDelete !== null} onOpenChange={(v) => !v && setToDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除该收藏？</AlertDialogTitle>
            <AlertDialogDescription>
              将删除收藏「{toDelete?.aldehyde?.name || '未知醛'} × {toDelete?.amine?.name || '未知胺'}」
              （{toDelete?.id}），关联的实验记录不会被删除。此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={confirmDelete}
              disabled={deleting}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {deleting ? '删除中…' : '确认删除'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
