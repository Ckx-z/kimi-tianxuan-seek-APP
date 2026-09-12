/**
 * 迷你图表（v1.9.4 UI 第 3 批：首页仪表盘化）。
 *
 * 纯内联 SVG，无第三方图表库：KPI 卡里的趋势一眼可见，且体积/依赖为零。
 * - `Sparkline`：折线 + 渐变面积（连续时间序列，如「近 8 周新增」）
 * - `MiniBars`：柱状（离散分组，如「各批次建议数」）
 * 数据必须真实：调用方只在有真实序列时传值，不要造数。
 */
import { useId } from 'react';

import { cn } from '@/lib/utils';

interface SparklineProps {
  values: number[];
  className?: string;
  /** 线色（默认主题主色） */
  stroke?: string;
}

export function Sparkline({ values, className, stroke }: SparklineProps) {
  const gid = useId().replace(/[:]/g, '');
  if (!values || values.length < 2) {
    return <div className={cn('h-6', className)} aria-hidden />;
  }
  const w = 100;
  const h = 28;
  const pad = 2;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const span = max - min || 1;
  const step = (w - pad * 2) / (values.length - 1);
  const pts = values.map((v, i) => {
    const x = pad + i * step;
    const y = h - pad - ((v - min) / span) * (h - pad * 2);
    return [x, y] as const;
  });
  const line = pts.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`).join(' ');
  const area = `${line} L${(w - pad).toFixed(2)},${h - pad} L${pad},${h - pad} Z`;
  const color = stroke ?? 'hsl(var(--primary))';

  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none"
         className={cn('h-6 w-full', className)} aria-hidden>
      <defs>
        <linearGradient id={`sp-${gid}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#sp-${gid})`} />
      <path d={line} fill="none" stroke={color} strokeWidth="1.6"
            strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      {pts.length > 0 && (
        <circle cx={pts[pts.length - 1][0]} cy={pts[pts.length - 1][1]} r="1.8"
                fill={color} />
      )}
    </svg>
  );
}

interface MiniBarsProps {
  values: number[];
  labels?: string[];
  className?: string;
}

export function MiniBars({ values, labels, className }: MiniBarsProps) {
  if (!values || values.length === 0) {
    return <div className={cn('h-6', className)} aria-hidden />;
  }
  const max = Math.max(...values, 1);
  return (
    <div className={cn('flex h-6 items-end gap-1', className)}
         title={labels ? labels.join(' · ') : undefined}>
      {values.map((v, i) => (
        <span
          key={`${labels?.[i] ?? i}`}
          className="flex-1 rounded-sm bg-primary/25 transition-colors last:bg-primary/70"
          style={{ height: `${Math.max(12, (v / max) * 100)}%` }}
          title={labels?.[i] ? `${labels[i]}：${v}` : String(v)}
        />
      ))}
    </div>
  );
}
