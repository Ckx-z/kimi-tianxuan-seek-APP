/**
 * 统一页面头部（v1.9.4 UI 第 3 批）。
 *
 * 全站页面标题区此前各写各的（字号/间距/渐变/右侧操作区不一致）。
 * 统一为：主标题（衬线学术签名，由 `main h1` 的 CSS 变量决定）+ 一句话副标题 +
 * 右侧主行动区；间距进入 8pt 节奏。
 */
import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

interface PageHeaderProps {
  title: string;
  subtitle?: string;
  /** 右侧操作区（按钮/状态徽章等） */
  actions?: ReactNode;
  /** 使用主题渐变标题（首页/我的/设置等「门面页」） */
  accent?: boolean;
  className?: string;
}

export function PageHeader({
  title, subtitle, actions, accent = false, className,
}: PageHeaderProps) {
  return (
    <div className={cn('flex flex-wrap items-end justify-between gap-3', className)}>
      <div className="min-w-0">
        <h1
          className={cn(
            'text-2xl font-semibold leading-tight',
            accent ? 'text-gradient-royal' : 'text-foreground',
          )}
        >
          {title}
        </h1>
        {subtitle && (
          <p className="mt-1 text-sm text-muted-foreground">{subtitle}</p>
        )}
      </div>
      {actions && (
        <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>
      )}
    </div>
  );
}
