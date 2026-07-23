/**
 * 实验记录时间线（右栏 3/5）
 * - 记录卡列表：日期/单体对/结果徽章（成膜紫/部分金/失败灰）/编号/条件摘要
 * - 预测快照 vs 实际结果对比（有 prediction_snapshot 时）
 * - 每卡操作：「放大」Dialog 大字号全字段详情；「删除」AlertDialog 确认后 DELETE
 * - 顶部：刷新按钮 + 记录数 + 「只看选中收藏」过滤开关
 */
import { useState } from 'react';
import { toast } from 'sonner';
import { Loader2, Maximize2, RefreshCw, Trash2 } from 'lucide-react';
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
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { deleteRecord, type RecordItem } from './api';

/** conditions 九键中文名 */
const CONDITION_LABELS: Record<string, string> = {
  solvent_1: '溶剂一',
  solvent_2: '溶剂二',
  eluent: '洗脱剂',
  modulator: '调制剂',
  catalyst: '催化剂',
  temperature_c: '温度（℃）',
  time_days: '时间（天）',
  vessel: '容器',
  addition_order: '加料顺序',
};

/** 结果徽章配置：成膜紫 / 部分金 / 失败灰 */
const OUTCOME_META: Record<string, { label: string; className: string }> = {
  film: { label: '成膜', className: 'bg-primary text-primary-foreground' },
  partial: { label: '部分成膜', className: 'bg-gold text-gold-foreground' },
  failed: { label: '失败', className: 'bg-muted text-muted-foreground' },
};

export interface RecordTimelineProps {
  records: RecordItem[];
  loading: boolean;
  /** 后端不可用（降级提示） */
  backendDown: boolean;
  /** 「只看选中收藏」开关状态（受控） */
  onlySelected: boolean;
  onOnlySelectedChange: (on: boolean) => void;
  /** 当前选中收藏 id（仅用于提示文案） */
  favoriteId: string;
  /** 刷新列表 */
  onRefresh: () => void;
}

/** 单体对显示名 */
function pairLabel(rec: RecordItem): string {
  const ald = rec.aldehyde?.name || rec.aldehyde?.smiles?.slice(0, 16) || '未知醛';
  const amine = rec.amine?.name || rec.amine?.smiles?.slice(0, 16) || '未知胺';
  return `${ald} + ${amine}`;
}

/** 条件摘要：拼接非空条件键值 */
function conditionsSummary(rec: RecordItem): string {
  const parts = Object.entries(rec.conditions || {})
    .filter(([, v]) => v !== '' && v != null)
    .map(([k, v]) => `${CONDITION_LABELS[k] ?? k}：${String(v)}`);
  return parts.length > 0 ? parts.join('；') : '未填写条件';
}

/** 预测快照 vs 实际结果 对比块 */
function PredictionCompare({ rec }: { rec: RecordItem }) {
  const snap = rec.prediction_snapshot;
  if (!snap || snap.score == null) return null;
  const outcomeLabel = OUTCOME_META[rec.outcome]?.label ?? rec.outcome;
  // 简单一致性判断：高分+成膜 / 低分+失败 视为一致
  const score = Number(snap.score);
  const consistent =
    (score >= 0.5 && rec.outcome === 'film') || (score < 0.5 && rec.outcome === 'failed');
  return (
    <div className="mt-2 rounded-lg border border-gold/50 bg-gold-muted px-3 py-2 text-xs">
      <span className="font-medium">预测快照：</span>
      评分 {score.toFixed(3)}
      {snap.std != null && `（±${Number(snap.std).toFixed(3)}）`}
      {snap.ood ? `｜OOD：${snap.ood}` : ''}
      <span className="mx-1.5">→</span>
      <span className="font-medium">实际：</span>
      {outcomeLabel}
      <span className={consistent ? 'ml-1.5 text-primary' : 'ml-1.5 text-muted-foreground'}>
        {consistent ? '✓ 与预测一致' : '· 与预测存在偏差'}
      </span>
    </div>
  );
}

export default function RecordTimeline({
  records,
  loading,
  backendDown,
  onlySelected,
  onOnlySelectedChange,
  favoriteId,
  onRefresh,
}: RecordTimelineProps) {
  /** 放大详情的记录 */
  const [detailRec, setDetailRec] = useState<RecordItem | null>(null);
  /** 待删除确认的记录 */
  const [deletingRec, setDeletingRec] = useState<RecordItem | null>(null);
  const [deleting, setDeleting] = useState(false);

  /** 确认删除 */
  const handleDelete = async () => {
    if (!deletingRec) return;
    setDeleting(true);
    try {
      await deleteRecord(deletingRec.record_id);
      toast.success(`记录 ${deletingRec.experiment_no} 已删除`);
      setDeletingRec(null);
      onRefresh();
    } catch {
      // 错误提示已由 api 封装弹出
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* 顶部工具栏：标题 / 记录数 / 过滤开关 / 刷新 */}
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold text-foreground">记录时间线</h2>
        <Badge variant="secondary">{records.length} 条记录</Badge>
        <div className="ml-auto flex items-center gap-3">
          <div className="flex items-center gap-2">
            <Switch
              id="only-selected"
              checked={onlySelected}
              onCheckedChange={onOnlySelectedChange}
              disabled={!favoriteId}
            />
            <Label htmlFor="only-selected" className="text-sm text-muted-foreground">
              只看选中收藏
            </Label>
          </div>
          <Button variant="outline" size="sm" onClick={onRefresh} disabled={loading}>
            {loading ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-1.5 h-4 w-4" />}
            刷新
          </Button>
        </div>
      </div>

      {/* 后端未连接降级提示（不白屏） */}
      {backendDown && (
        <div className="rounded-xl border border-dashed border-gold/50 bg-gold-muted p-8 text-center text-sm text-muted-foreground">
          后端未连接，无法加载实验记录。请确认 FastAPI 服务已启动（http://localhost:8000）后点击「刷新」。
        </div>
      )}

      {/* 加载态 */}
      {!backendDown && loading && (
        <div className="flex items-center justify-center rounded-xl border border-border bg-card p-12 text-muted-foreground">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" /> 加载中……
        </div>
      )}

      {/* 空态 */}
      {!backendDown && !loading && records.length === 0 && (
        <div className="rounded-xl border border-dashed border-border bg-card p-12 text-center text-muted-foreground">
          {onlySelected && favoriteId ? '该收藏下暂无实验记录' : '暂无实验记录，在左侧录入第一条吧'}
        </div>
      )}

      {/* 记录卡列表（时间倒序展示：后端升序，前端反转） */}
      {!backendDown && !loading && records.length > 0 && (
        <div className="space-y-3">
          {[...records].reverse().map((rec) => {
            const meta = OUTCOME_META[rec.outcome] ?? OUTCOME_META.failed;
            return (
              <div key={rec.record_id} className="rounded-xl border border-border bg-card p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm text-muted-foreground">{rec.date}</span>
                  <span className="font-medium text-foreground">{pairLabel(rec)}</span>
                  <Badge className={meta.className}>{meta.label}</Badge>
                  <span className="text-xs text-muted-foreground">编号 {rec.experiment_no}</span>
                  <div className="ml-auto flex gap-1">
                    <Button variant="ghost" size="sm" onClick={() => setDetailRec(rec)}>
                      <Maximize2 className="mr-1 h-3.5 w-3.5" /> 放大
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-destructive hover:text-destructive"
                      onClick={() => setDeletingRec(rec)}
                    >
                      <Trash2 className="mr-1 h-3.5 w-3.5" /> 删除
                    </Button>
                  </div>
                </div>
                <p className="mt-2 line-clamp-2 text-sm text-muted-foreground">{conditionsSummary(rec)}</p>
                <PredictionCompare rec={rec} />
              </div>
            );
          })}
        </div>
      )}

      {/* 放大详情 Dialog：大字号全字段 */}
      <Dialog open={detailRec !== null} onOpenChange={(open) => !open && setDetailRec(null)}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
          {detailRec && (
            <>
              <DialogHeader>
                <DialogTitle className="text-xl">
                  实验记录 {detailRec.experiment_no}
                </DialogTitle>
                <DialogDescription>
                  {detailRec.date}｜{detailRec.record_id}
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-4 text-base">
                <div>
                  <p className="text-sm text-muted-foreground">单体对</p>
                  <p className="text-lg font-medium">{pairLabel(detailRec)}</p>
                  <p className="mt-1 break-all text-sm text-muted-foreground">
                    醛 SMILES：{detailRec.aldehyde?.smiles || '—'}
                    <br />
                    胺 SMILES：{detailRec.amine?.smiles || '—'}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <p className="text-sm text-muted-foreground">结果</p>
                  <Badge className={(OUTCOME_META[detailRec.outcome] ?? OUTCOME_META.failed).className}>
                    {(OUTCOME_META[detailRec.outcome] ?? OUTCOME_META.failed).label}
                  </Badge>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">反应条件</p>
                  <div className="mt-1 grid grid-cols-2 gap-x-6 gap-y-1.5">
                    {Object.entries(CONDITION_LABELS).map(([key, label]) => {
                      const v = detailRec.conditions?.[key];
                      return (
                        <p key={key} className="text-sm">
                          <span className="text-muted-foreground">{label}：</span>
                          {v !== '' && v != null ? String(v) : '—'}
                        </p>
                      );
                    })}
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <p className="text-sm">
                    <span className="text-muted-foreground">机械强度：</span>
                    {detailRec.strength || '—'}
                  </p>
                  <p className="text-sm">
                    <span className="text-muted-foreground">操作人：</span>
                    {detailRec.operator || '—'}
                  </p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">备注</p>
                  <p className="mt-1 whitespace-pre-wrap text-base">{detailRec.notes || '—'}</p>
                </div>
                {detailRec.prediction_snapshot && detailRec.prediction_snapshot.score != null && (
                  <div className="rounded-lg border border-gold/50 bg-gold-muted px-3 py-2 text-sm">
                    <span className="font-medium">预测快照：</span>
                    评分 {Number(detailRec.prediction_snapshot.score).toFixed(3)}
                    {detailRec.prediction_snapshot.std != null &&
                      `（±${Number(detailRec.prediction_snapshot.std).toFixed(3)}）`}
                    {detailRec.prediction_snapshot.ood
                      ? `｜OOD：${detailRec.prediction_snapshot.ood}`
                      : ''}
                  </div>
                )}
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* 删除确认 AlertDialog */}
      <AlertDialog open={deletingRec !== null} onOpenChange={(open) => !open && setDeletingRec(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除该实验记录？</AlertDialogTitle>
            <AlertDialogDescription>
              将删除编号「{deletingRec?.experiment_no}」（{deletingRec?.record_id}）的记录，此操作不可恢复。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>取消</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete} disabled={deleting}>
              {deleting ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : null}
              确认删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
