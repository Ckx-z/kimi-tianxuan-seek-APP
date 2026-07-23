/**
 * 迭代建议卡片：类型徽章、标题、detail、置信度徽章、证据引用、未校验引用警示、采纳按钮
 */
import { useState } from 'react';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { CheckCircle2, Loader2, TriangleAlert } from 'lucide-react';
import type { Suggestion } from '@/types';
import { adoptSuggestion } from './api';

/** 建议类型 → 中文徽章 */
const TYPE_LABEL: Record<string, string> = {
  condition_adjust: '调参',
  new_candidate: '新候选',
  literature: '文献',
};

/** 置信度等级 → 中文与样式（high 紫 / medium 金 / low 灰） */
const CONF_META: Record<string, { label: string; className: string }> = {
  high: { label: '高置信', className: 'bg-primary text-primary-foreground' },
  medium: { label: '中置信', className: 'bg-gold text-gold-foreground' },
  low: { label: '低置信', className: 'bg-muted text-muted-foreground' },
};

interface EvidenceRef {
  kind?: string;
  ref?: string;
  note?: string;
}

interface ConfidenceInfo {
  level?: string;
  reason?: string;
}

interface Adjustment {
  field?: string;
  from?: string;
  to?: string;
  rationale?: string;
}

const KIND_LABEL: Record<string, string> = {
  experiment_record: '实验记录',
  literature: '文献',
  prediction: '预测',
};

interface SuggestionCardProps {
  suggestion: Suggestion;
  /** 采纳成功后回调（用于刷新方案列表等） */
  onAdopted?: (planSeq: number | undefined) => void;
}

export function SuggestionCard({ suggestion, onAdopted }: SuggestionCardProps) {
  const [adopting, setAdopting] = useState(false);
  const [adopted, setAdopted] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);

  const payload = (suggestion.payload ?? {}) as Suggestion['payload'] & {
    adjustments?: Adjustment[];
    confidence?: ConfidenceInfo;
    unverified_refs?: EvidenceRef[];
  };
  const evidenceRefs = (suggestion.evidence_refs ?? []) as EvidenceRef[];
  const unverified = payload.unverified_refs ?? [];
  const adjustments = payload.adjustments ?? [];
  const conf = payload.confidence;
  const confMeta = CONF_META[conf?.level ?? ''] ?? CONF_META.low;
  // 已采纳：后端状态 adopted 或本页刚采纳
  const isAdopted = adopted || suggestion.status === 'adopted';
  const isNew = suggestion.status === 'new' && !adopted;

  const handleAdopt = async () => {
    setAdopting(true);
    try {
      const plan = await adoptSuggestion(suggestion.suggestion_id);
      setAdopted(true);
      setDialogOpen(false);
      toast.success(plan?.seq ? `已生成 方案 v${plan.seq}` : '已采纳并生成方案');
      onAdopted?.(plan?.seq);
    } catch {
      // 错误 toast 已在 api 层弹出
    } finally {
      setAdopting(false);
    }
  };

  return (
    <Card
      className={
        isAdopted
          ? 'border-primary/50 opacity-60' // 已采纳：变淡 + 紫边
          : 'bg-card'
      }
    >
      <CardContent className="space-y-3 p-4">
        {/* 头部：类型徽章 + 标题 + 置信度 */}
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" className="border-primary/40 text-primary">
            {TYPE_LABEL[suggestion.type] ?? suggestion.type}
          </Badge>
          <span className="font-medium text-foreground">
            {payload.title || '（无标题建议）'}
          </span>
          {conf?.level && (
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Badge className={confMeta.className}>{confMeta.label}</Badge>
                </TooltipTrigger>
                {conf.reason && (
                  <TooltipContent className="max-w-xs">{conf.reason}</TooltipContent>
                )}
              </Tooltip>
            </TooltipProvider>
          )}
          {isAdopted && (
            <Badge variant="outline" className="border-primary/40 text-primary">
              <CheckCircle2 className="mr-1 h-3 w-3" />已采纳
            </Badge>
          )}
        </div>

        {/* detail：调整明细 */}
        {adjustments.length > 0 && (
          <ul className="space-y-1.5 text-sm text-muted-foreground">
            {adjustments.map((adj, i) => (
              <li key={i} className="rounded-md bg-muted/50 px-3 py-2">
                {adj.to || adj.rationale || adj.field || '（空调整项）'}
                {adj.rationale && adj.to && (
                  <span className="block text-xs opacity-80">理由：{adj.rationale}</span>
                )}
              </li>
            ))}
          </ul>
        )}

        {/* 证据引用 */}
        {evidenceRefs.length > 0 && (
          <div className="space-y-1">
            <p className="text-xs font-medium text-muted-foreground">证据引用</p>
            <ul className="space-y-0.5 text-xs text-muted-foreground">
              {evidenceRefs.map((e, i) => (
                <li key={i}>
                  <span className="text-gold">[{KIND_LABEL[e.kind ?? ''] ?? e.kind}]</span>{' '}
                  {e.ref}
                  {e.note ? ` — ${e.note}` : ''}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* 未通过校验引用警示 */}
        {unverified.length > 0 && (
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <p className="flex items-center gap-1 text-xs text-gold">
                  <TriangleAlert className="h-3.5 w-3.5" />
                  {unverified.length} 条引用未通过校验
                </p>
              </TooltipTrigger>
              <TooltipContent className="max-w-sm">
                <ul className="space-y-1 text-xs">
                  {unverified.map((e, i) => (
                    <li key={i}>{e.ref}{e.note ? ` — ${e.note}` : ''}</li>
                  ))}
                </ul>
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}

        {/* 采纳按钮（仅 status=new 显示） */}
        {isNew && (
          <AlertDialog open={dialogOpen} onOpenChange={setDialogOpen}>
            <AlertDialogTrigger asChild>
              <Button size="sm" className="bg-primary text-primary-foreground">
                ✅ 采纳并生成方案
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>确认采纳该建议？</AlertDialogTitle>
                <AlertDialogDescription>
                  将基于「{payload.title || suggestion.suggestion_id}」生成新的实验方案卡，生成后不可撤销。
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel disabled={adopting}>取消</AlertDialogCancel>
                <AlertDialogAction onClick={handleAdopt} disabled={adopting}>
                  {adopting && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}
                  确认采纳
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        )}
      </CardContent>
    </Card>
  );
}
