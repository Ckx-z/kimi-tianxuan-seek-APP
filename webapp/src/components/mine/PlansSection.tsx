/**
 * 「我的方案库」区块
 * - 方案卡列表：方案 vN + 模板名 + 时间
 * - 点击展开查看完整方案（单体 / 条件 / 步骤 简洁渲染）
 */
import { useState } from 'react';
import { ChevronDown, ClipboardList } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import type { PlanItem } from './api';

/** 条件键名中文映射（未知键原样显示） */
const CONDITION_LABELS: Record<string, string> = {
  solvent: '溶剂',
  modulator: '调制剂',
  catalyst: '催化剂',
  temperature_c: '温度 (°C)',
  time_days: '时间 (天)',
  vessel: '容器',
};

/** 单个方案卡（可展开） */
function PlanRow({ plan }: { plan: PlanItem }) {
  const [open, setOpen] = useState(false);
  const card = plan.plan_card;
  const conditions = (card?.conditions ?? {}) as Record<string, unknown>;

  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card">
      {/* 折叠头：点击展开/收起 */}
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/50"
        onClick={() => setOpen((v) => !v)}
      >
        <div className="flex min-w-0 items-center gap-3">
          <Badge className="shrink-0 bg-primary text-primary-foreground">方案 v{plan.seq ?? '?'}</Badge>
          <span className="truncate text-sm font-medium text-foreground">
            {plan.template_name || card?.template || '未命名方案'}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <span className="text-xs text-muted-foreground">{plan.created_at || plan.plan_id}</span>
          <ChevronDown
            className={`h-4 w-4 text-muted-foreground transition-transform ${open ? 'rotate-180' : ''}`}
          />
        </div>
      </button>

      {/* 展开内容：完整方案 */}
      {open && (
        <div className="space-y-4 border-t border-border px-4 py-4">
          {!card ? (
            <p className="text-sm text-muted-foreground">该方案暂无方案卡内容。</p>
          ) : (
            <>
              {/* 单体 */}
              <div className="grid gap-3 sm:grid-cols-2">
                {(
                  [
                    ['醛单体', card.aldehyde],
                    ['胺单体', card.amine],
                  ] as const
                ).map(([label, m]) => (
                  <div key={label} className="rounded-lg border border-border bg-muted/40 p-3 text-sm">
                    <div className="text-xs text-muted-foreground">{label}</div>
                    <div className="mt-1 font-medium text-foreground">{m?.name || '—'}</div>
                    <div className="mt-1 break-all font-mono text-xs text-muted-foreground">
                      {m?.smiles || '—'}
                    </div>
                  </div>
                ))}
              </div>

              {/* 条件 */}
              {Object.keys(conditions).length > 0 && (
                <div>
                  <div className="mb-1.5 text-xs font-semibold text-muted-foreground">成膜条件</div>
                  <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    {Object.entries(conditions).map(([k, v]) => (
                      <div key={k} className="rounded-lg bg-gold-muted/40 px-3 py-2 text-sm">
                        <span className="text-xs text-muted-foreground">
                          {CONDITION_LABELS[k] ?? k}：
                        </span>
                        <span className="text-foreground">{String(v)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* 步骤 */}
              {Array.isArray(card.steps) && card.steps.length > 0 && (
                <div>
                  <div className="mb-1.5 text-xs font-semibold text-muted-foreground">操作步骤</div>
                  <ol className="list-decimal space-y-1 pl-5 text-sm text-foreground">
                    {card.steps.map((s, i) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ol>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

export function PlansSection({ plans, loading }: { plans: PlanItem[]; loading: boolean }) {
  if (loading) {
    return (
      <div className="space-y-3">
        {[0, 1].map((i) => (
          <Skeleton key={i} className="h-14 w-full rounded-xl" />
        ))}
      </div>
    );
  }

  if (plans.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border bg-card p-12 text-center text-sm text-muted-foreground">
        <ClipboardList className="mx-auto mb-2 h-6 w-6 text-muted-foreground" />
        暂无方案：在迭代页采纳建议后，方案会保存在这里。
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {plans.map((p) => (
        <PlanRow key={p.plan_id} plan={p} />
      ))}
    </div>
  );
}
