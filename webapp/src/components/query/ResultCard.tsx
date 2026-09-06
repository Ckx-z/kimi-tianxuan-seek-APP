/**
 * 打分结果大卡：主分数 + 树/GNN 分量小卡 + OOD 横幅 + tree_route 路由说明
 * v1.8.0：新增「反馈打分不合理」入口（三档修正 + 理由 → /api/gnn/feedback）
 */
import { useState } from 'react';
import { Flag, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { PredictResult } from './api';

interface Props {
  result: PredictResult | null;
  loading: boolean;
  /** v1.8.0：当前输入组合（反馈打分不合理用） */
  aldSmiles?: string;
  amineSmiles?: string;
}

const LABEL_OPTIONS = [
  { value: 1.0, label: '成膜（1.0）', desc: '文献/实验证实可成膜' },
  { value: 0.5, label: '边界（0.5）', desc: '结果不确定/有条件成膜' },
  { value: 0.0, label: '不成膜（0.0）', desc: '证实不可成膜' },
];

/** 反馈打分不合理弹窗：三档修正 + 理由 → 反馈队列 */
function FeedbackDialog({
  open, onClose, ald, amine,
}: {
  open: boolean;
  onClose: () => void;
  ald: string;
  amine: string;
}) {
  const [label, setLabel] = useState<number | null>(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (label == null) {
      toast.error('请选择正确档位');
      return;
    }
    setBusy(true);
    try {
      const res = await fetch('/api/gnn/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ald_smiles: ald, amine_smiles: amine, label, note: note.trim(),
          source: 'score_correction',
        }),
      });
      if (!res.ok) {
        let msg = `提交失败（${res.status}）`;
        try {
          const data = await res.json();
          if (typeof data?.detail === 'string') msg = data.detail;
        } catch {
          /* keep */
        }
        toast.error(msg);
        return;
      }
      toast.success('已入反馈队列：可在设置页「GNN 模型演进」确认并用于重训');
      onClose();
    } catch {
      toast.error('无法连接后端服务');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && !busy && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>反馈打分不合理</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <p className="truncate text-xs text-muted-foreground" title={`${ald} + ${amine}`}>
            {ald.slice(0, 30)}… + {amine.slice(0, 30)}…
          </p>
          <div className="space-y-1.5">
            <Label>正确档位</Label>
            <div className="space-y-1">
              {LABEL_OPTIONS.map((o) => (
                <label
                  key={o.value}
                  className={`flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2 text-sm ${
                    label === o.value
                      ? 'border-gold bg-gold-muted/40'
                      : 'border-border hover:bg-muted/40'
                  }`}
                >
                  <input
                    type="radio"
                    name="gnn-fb-label"
                    className="mt-1"
                    checked={label === o.value}
                    onChange={() => setLabel(o.value)}
                  />
                  <span>
                    <span className="font-medium">{o.label}</span>
                    <span className="block text-xs text-muted-foreground">{o.desc}</span>
                  </span>
                </label>
              ))}
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="fb-note">理由（文献标题/实验编号等，供复核与溯源）</Label>
            <Textarea
              id="fb-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={3}
              placeholder="如：文献 10.xxxx/xxxx 报道该组合可成膜"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button onClick={() => void submit()} disabled={busy}>
            {busy && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
            提交反馈
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** OOD 横幅：out 红色 / 其他非 in 黄色 */
function OodBanner({ ood }: { ood: PredictResult['ood'] }) {
  // 后端正常级别为 "in" 或 "none"，其余按警告/不适用处理
  if (!ood || ood.level === 'in' || ood.level === 'none') return null;
  const isOut = ood.level === 'out';
  return (
    <div
      className={
        isOut
          ? 'rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300'
          : 'rounded-lg border border-yellow-300 bg-yellow-50 p-3 text-sm text-yellow-800 dark:border-yellow-900 dark:bg-yellow-950/40 dark:text-yellow-300'
      }
    >
      <div className="font-semibold">
        {isOut ? '⛔ 分布外（OOD=out）：模型不适用' : '⚠️ 分布外警告（OOD=warning）：结果可信度较低'}
      </div>
      {ood.reasons?.length > 0 && (
        <ul className="mt-1 list-inside list-disc space-y-0.5">
          {ood.reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** 分量小卡（树 / GNN） */
function SubScoreCard({ label, score, std }: { label: string; score: number | null; std: number | null }) {
  return (
    <div className="rounded-lg border bg-card p-3 text-center">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-xl font-semibold text-foreground">
        {score == null ? '—' : score.toFixed(3)}
        {score != null && std != null && (
          <span className="ml-1 text-sm font-normal text-muted-foreground">±{std.toFixed(3)}</span>
        )}
      </div>
    </div>
  );
}

export default function ResultCard({ result, loading, aldSmiles, amineSmiles }: Props) {
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  // 加载态
  if (loading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>打分结果</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <Skeleton className="h-20 w-full" />
          <div className="grid grid-cols-2 gap-3">
            <Skeleton className="h-16" />
            <Skeleton className="h-16" />
          </div>
        </CardContent>
      </Card>
    );
  }

  // 空态
  if (!result) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>打分结果</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="rounded-lg border border-dashed p-10 text-center text-muted-foreground">
            输入醛 / 胺单体后点击「开始打分」，结果将显示在这里
          </div>
        </CardContent>
      </Card>
    );
  }

  const noScore = result.score == null;
  const canFeedback = Boolean(aldSmiles && amineSmiles);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center justify-between gap-2">
          <span>打分结果</span>
          <span className="text-xs font-normal text-muted-foreground">
            {result.score_policy === 'max_tree_gnn'
              ? '取分策略 max_tree_gnn（两模型较高值）'
              : result.score_policy === 'max_tree_gnn_redline'
                ? '取分策略 max_tree_gnn_redline（低交联度红线 + 组合外推收缩）'
                : `取分策略 ${result.score_policy}`}
            {result.score_flags?.divergence && (
              <span className="ml-2 text-amber-600 dark:text-amber-400">
                ⚠ 两模型分歧较大，已按保守口径取分
              </span>
            )}
            {result.score_flags?.gnn_pair_unseen && (
              <span className="ml-2 text-amber-600 dark:text-amber-400">
                ⚠ 该组合未在训练集中出现，GNN 分已收缩
              </span>
            )}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <OodBanner ood={result.ood} />

        {/* 主分数 */}
        <div className="rounded-xl border border-gold/50 bg-gold-muted p-6 text-center">
          {noScore ? (
            <>
              <div className="text-3xl font-bold text-foreground">⛔ 模型不适用</div>
              {result.ood?.reasons?.length > 0 && (
                <p className="mt-2 text-sm text-muted-foreground">{result.ood.reasons.join('；')}</p>
              )}
            </>
          ) : (
            <>
              <div className="text-sm text-muted-foreground">成膜评分（越高越好）</div>
              <div className="mt-1 text-4xl font-bold text-primary sm:text-5xl">{result.score!.toFixed(3)}</div>
            </>
          )}
        </div>

        {/* 树 / GNN 分量 */}
        <div className="grid grid-cols-2 gap-3">
          <SubScoreCard label="树模型分量" score={result.tree_score} std={result.tree_std} />
          <div>
            <SubScoreCard label="GNN 分量" score={result.gnn_score} std={result.gnn_std} />
            {result.gnn_model_version && (
              <p className="mt-1 text-center text-[11px] text-muted-foreground">
                {result.gnn_model_version}
              </p>
            )}
          </div>
        </div>

        {/* 反馈打分不合理（v1.8.0） */}
        {canFeedback && (
          <Button
            variant="outline"
            size="sm"
            className="w-full"
            onClick={() => setFeedbackOpen(true)}
          >
            <Flag className="mr-1.5 h-3.5 w-3.5" />
            反馈打分不合理（用于 GNN 修正重训）
          </Button>
        )}

        {/* tree_route 路由说明 */}
        {result.tree_route && (
          <p className="text-xs text-muted-foreground">
            树模型路由：{result.tree_route}
            {result.tree_model_name ? `（${result.tree_model_name}）` : ''}
          </p>
        )}
      </CardContent>

      {canFeedback && (
        <FeedbackDialog
          open={feedbackOpen}
          onClose={() => setFeedbackOpen(false)}
          ald={aldSmiles!}
          amine={amineSmiles!}
        />
      )}
    </Card>
  );
}
