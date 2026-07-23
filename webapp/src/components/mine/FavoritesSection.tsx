/**
 * 「我的收藏」卡片墙
 * - 卡片：醛名 × 胺名大字 + SMILES 小字 + 最新预测分徽章 + 创建时间 + 删除按钮
 * - 点击卡片弹出详情 Dialog：单体信息 / 预测快照 / 文献列表 / 该收藏的实验记录简表
 * - 删除经 AlertDialog 确认后调用 DELETE /api/favorites/{id}
 */
import { useEffect, useState } from 'react';
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
  type RecordItem,
  type ReferenceItem,
} from './api';

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

/** 收藏详情 Dialog */
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

  // 打开时拉取该收藏关联的实验记录
  useEffect(() => {
    if (!open || !fav) return;
    let cancelled = false;
    setRecLoading(true);
    fetchRecordsByFavorite(fav.id)
      .then((list) => !cancelled && setRecords(list))
      .catch(() => !cancelled && setRecords([]))
      .finally(() => !cancelled && setRecLoading(false));
    return () => {
      cancelled = true;
    };
  }, [open, fav]);

  if (!fav) return null;
  const p = fav.latest_prediction;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="text-gradient-royal">
            {fav.aldehyde?.name || '未知醛'} × {fav.amine?.name || '未知胺'}
          </DialogTitle>
          <DialogDescription>
            收藏编号 {fav.id} · 创建于 {fav.created_at || '未知时间'}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-5">
          {/* 单体信息 */}
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
                </div>
              ))}
            </div>
            {fav.notes && (
              <p className="mt-2 rounded-lg bg-gold-muted/40 px-3 py-2 text-xs text-muted-foreground">
                备注：{fav.notes}
              </p>
            )}
          </section>

          {/* 预测快照 */}
          <section>
            <h3 className="mb-2 text-sm font-semibold text-foreground">最新预测快照</h3>
            {p && typeof p.score === 'number' ? (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {(
                  [
                    ['综合分', p.score?.toFixed(3)],
                    ['树模型', typeof p.tree_score === 'number' ? p.tree_score.toFixed(3) : '—'],
                    ['GNN', typeof p.gnn_score === 'number' ? p.gnn_score.toFixed(3) : '—'],
                    ['不确定度', typeof p.tree_std === 'number' ? `±${p.tree_std.toFixed(3)}` : '—'],
                  ] as const
                ).map(([label, value]) => (
                  <div key={label} className="rounded-lg border border-border bg-muted/40 p-3 text-center">
                    <div className="text-xs text-muted-foreground">{label}</div>
                    <div className="mt-1 text-lg font-semibold text-primary">{value}</div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">尚未打分，可在查询页对该组合进行预测。</p>
            )}
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
