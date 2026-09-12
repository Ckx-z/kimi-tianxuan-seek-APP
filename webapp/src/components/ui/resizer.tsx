/**
 * 拖拽分隔条（v1.9.3 问题 4.3）：
 * 竖直分隔条，拖动调整相邻容器宽度；双击恢复默认；←/→ 键盘微调。
 * 仅在 md 断点以上显示（窄屏布局由页面自身的响应式规则处理）。
 */
import { GripVertical } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { ResizableWidth } from '@/hooks/useResizableWidth';

export function Resizer({
  handleProps,
  dragging,
  label,
  className,
}: {
  handleProps: ResizableWidth['handleProps'];
  dragging?: boolean;
  label: string;
  className?: string;
}) {
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      tabIndex={0}
      title={`${label}：拖动调整，双击恢复默认，←/→ 微调`}
      className={cn(
        'group hidden shrink-0 cursor-col-resize select-none items-center justify-center md:flex',
        'w-2 rounded-full transition-colors',
        dragging ? 'bg-gold/70' : 'bg-transparent hover:bg-gold/40',
        'focus:outline-none focus-visible:ring-1 focus-visible:ring-gold',
        className,
      )}
      {...handleProps}
    >
      <GripVertical
        className={cn(
          'h-4 w-4 transition-opacity',
          dragging ? 'text-gold-foreground opacity-100'
            : 'text-muted-foreground opacity-0 group-hover:opacity-100',
        )}
      />
    </div>
  );
}
