/**
 * 已生成方案卡片：折叠摘要 + Dialog 展开完整方案卡（步骤 + checklist，大字号）
 */
import { useState } from 'react';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { Plan } from '@/types';

interface Adjustment {
  field?: string;
  from?: string;
  to?: string;
  rationale?: string;
}

interface ChecklistItem {
  item?: string;
  detail?: string;
}

interface PlanCardBody {
  template?: string;
  steps?: string[];
  checklist?: ChecklistItem[];
  monomer_hints?: string[];
  conditions?: Record<string, unknown>;
  aldehyde?: { name?: string };
  amine?: { name?: string };
}

export function PlanCardItem({ plan }: { plan: Plan }) {
  const [open, setOpen] = useState(false);
  const card = (plan.plan_card ?? {}) as PlanCardBody;
  const adjustments = (plan.adjustments_applied ?? []) as Adjustment[];
  const createdAt = typeof plan.created_at === 'string' ? plan.created_at : '';

  return (
    <>
      {/* 摘要卡：点击展开完整方案 */}
      <Card
        className="cursor-pointer bg-card transition-colors hover:border-primary/50"
        onClick={() => setOpen(true)}
      >
        <CardContent className="space-y-2 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold text-primary">方案 v{plan.seq}</span>
            <Badge variant="outline">{plan.template_name || card.template || '默认模板'}</Badge>
            <span className="text-xs text-muted-foreground">{plan.plan_id}</span>
            {createdAt && (
              <span className="text-xs text-muted-foreground">
                {createdAt.replace('T', ' ').slice(0, 16)}
              </span>
            )}
          </div>

          {/* 本次调整区块（金色） */}
          {adjustments.length > 0 && (
            <div className="rounded-md border border-gold/50 bg-gold-muted p-3">
              <p className="mb-1 text-xs font-medium text-gold-foreground">本次调整</p>
              <ul className="space-y-1 text-xs text-gold-foreground/90">
                {adjustments.map((a, i) => (
                  <li key={i}>{a.to || a.rationale || '（空调整项）'}</li>
                ))}
              </ul>
            </div>
          )}
          <p className="text-xs text-muted-foreground">点击查看完整方案卡 →</p>
        </CardContent>
      </Card>

      {/* 完整方案卡 Dialog（大字号） */}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle className="text-xl">
              方案 v{plan.seq} · {plan.template_name || card.template || ''}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-5 text-base leading-relaxed">
            {(card.aldehyde?.name || card.amine?.name) && (
              <section>
                <h3 className="mb-1 font-semibold text-foreground">单体对</h3>
                <p className="text-muted-foreground">
                  {card.aldehyde?.name ?? '—'} ＋ {card.amine?.name ?? '—'}
                </p>
              </section>
            )}

            {card.steps && card.steps.length > 0 && (
              <section>
                <h3 className="mb-2 font-semibold text-foreground">实验步骤</h3>
                <ol className="list-decimal space-y-1.5 pl-6 text-foreground/90">
                  {card.steps.map((s, i) => (
                    <li key={i}>{s}</li>
                  ))}
                </ol>
              </section>
            )}

            {card.checklist && card.checklist.length > 0 && (
              <section>
                <h3 className="mb-2 font-semibold text-foreground">防错清单</h3>
                <ul className="space-y-2">
                  {card.checklist.map((c, i) => (
                    <li key={i} className="rounded-md border border-gold/50 bg-gold-muted px-3 py-2">
                      <span className="font-medium text-gold-foreground">✓ {c.item}</span>
                      {c.detail && (
                        <span className="block text-sm text-gold-foreground/85">{c.detail}</span>
                      )}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {card.monomer_hints && card.monomer_hints.length > 0 && (
              <section>
                <h3 className="mb-1 font-semibold text-foreground">单体提示</h3>
                <ul className="list-disc space-y-1 pl-6 text-sm text-muted-foreground">
                  {card.monomer_hints.map((h, i) => (
                    <li key={i}>{h}</li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
