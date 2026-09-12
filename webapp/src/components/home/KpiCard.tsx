/**
 * 仪表盘 KPI 卡（v1.9.4 UI 第 3 批）。
 *
 * 设计要点（借 Tremor 的仪表化思路、浅色实现）：
 * - 标签弱化（小字 + 字距）→ 数值成为视觉焦点（等宽数字，切换时跳动感更小）
 * - 图标收进柔和色块（不再是「文字旁一个飘着的图标」）
 * - 底部一行：真实环比 + 迷你趋势（有真实序列才画，不造数）
 * - 整卡可点（链接），hover 抬升 + 描边变亮
 */
import type { LucideIcon } from 'lucide-react';
import { Link } from 'react-router';

import { MiniBars, Sparkline } from '@/components/home/Sparkline';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

export interface KpiCardProps {
  label: string;
  value: number;
  icon: LucideIcon;
  to: string;
  loading?: boolean;
  /** 单位或后缀（弱化显示，如「条」） */
  unit?: string;
  /** 真实时间序列（近 N 期）→ 画折线 */
  series?: number[];
  /** 真实离散分组 → 画柱状（与 series 二选一） */
  bars?: { values: number[]; labels?: string[] };
  /** 环比提示，如 { text: '近 8 周', delta: 5 } */
  trend?: { text: string; delta: number };
  /** 无趋势数据时的说明文案 */
  hint?: string;
}

export function KpiCard({
  label, value, icon: Icon, to, loading, unit, series, bars, trend, hint,
}: KpiCardProps) {
  const deltaPositive = (trend?.delta ?? 0) > 0;
  const deltaNegative = (trend?.delta ?? 0) < 0;

  return (
    <Link to={to} className="group block rounded-2xl outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">
      <Card
        className={cn(
          'card-interactive gap-3 py-4 transition-all duration-200',
          'group-hover:-translate-y-0.5 group-hover:border-primary/40',
        )}
      >
        <CardContent className="space-y-2 px-5">
          <div className="flex items-start justify-between gap-2">
            <span className="text-xs font-medium tracking-wide text-muted-foreground">
              {label}
            </span>
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary transition-colors group-hover:bg-primary/15">
              <Icon className="h-4 w-4" />
            </span>
          </div>

          <div className="flex items-baseline gap-1">
            {loading ? (
              <Skeleton className="h-8 w-16" />
            ) : (
              <>
                <span className="num-data text-[28px] font-semibold leading-none text-foreground">
                  {value}
                </span>
                {unit && (
                  <span className="text-xs text-muted-foreground">{unit}</span>
                )}
              </>
            )}
          </div>

          <div className="flex items-end justify-between gap-3 pt-0.5">
            <span className="text-[11px] leading-tight text-muted-foreground">
              {trend ? (
                <>
                  {trend.text}{' '}
                  <span
                    className={cn(
                      'num-data font-medium',
                      deltaPositive && 'text-success',
                      deltaNegative && 'text-destructive',
                    )}
                  >
                    {deltaPositive ? '+' : ''}{trend.delta}
                  </span>
                </>
              ) : (hint ?? '')}
            </span>
            <span className="w-24 shrink-0">
              {series && series.length > 1 ? (
                <Sparkline values={series} className="h-7" />
              ) : bars && bars.values.length > 0 ? (
                <MiniBars values={bars.values} labels={bars.labels} className="h-7" />
              ) : null}
            </span>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
