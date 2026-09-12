/**
 * 可拖拽宽度（v1.9.3 问题 4.3）。
 *
 * - Pointer Events（鼠标 + 触屏 + 触控笔通用），拖动时给 body 加
 *   `select-none` 防止选中文字；
 * - 宽度 clamp 到 [min, max]，并持久化到 localStorage（key 由调用方给出，
 *   互不干扰）；
 * - 支持键盘 ←/→ 微调（可访问性）与双击恢复默认宽度（由 UI 层调用 reset）。
 */
import { useCallback, useEffect, useRef, useState } from 'react';

export interface UseResizableWidthOptions {
  /** localStorage 键（建议 `cof.layout.<区域>`） */
  storageKey: string;
  defaultWidth: number;
  min: number;
  max: number;
  /** 手柄在容器左侧时置 true（向左拖动 = 变宽） */
  invert?: boolean;
  /** 键盘微调步长（px） */
  step?: number;
  /**
   * 允许「未设置」状态（width === 0 = 使用默认布局，如铺满）。
   * 首次拖动时以相邻兄弟元素的实际像素宽度为起点，避免跳变。
   */
  allowZero?: boolean;
}

export interface ResizableWidth {
  width: number;
  dragging: boolean;
  reset: () => void;
  /** 展开到分隔条元素上的事件（含键盘与无障碍属性） */
  handleProps: {
    onPointerDown: (e: React.PointerEvent) => void;
    onPointerMove: (e: React.PointerEvent) => void;
    onPointerUp: (e: React.PointerEvent) => void;
    onPointerCancel: (e: React.PointerEvent) => void;
    onKeyDown: (e: React.KeyboardEvent) => void;
    onDoubleClick: () => void;
  };
}

function clamp(v: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, v));
}

export function useResizableWidth({
  storageKey,
  defaultWidth,
  min,
  max,
  invert = false,
  step = 16,
  allowZero = false,
}: UseResizableWidthOptions): ResizableWidth {
  const [width, setWidth] = useState<number>(() => {
    if (typeof window === 'undefined') return defaultWidth;
    const raw = window.localStorage.getItem(storageKey);
    if (raw === null) return defaultWidth;
    const n = Number(raw);
    if (!Number.isFinite(n)) return defaultWidth;
    if (allowZero && n === 0) return 0;
    return clamp(n, min, max);
  });
  const [dragging, setDragging] = useState(false);
  const startRef = useRef({ x: 0, w: defaultWidth });

  const persist = useCallback((value: number) => {
    try {
      window.localStorage.setItem(storageKey, String(Math.round(value)));
    } catch {
      /* 隐私模式等写入失败忽略 */
    }
  }, [storageKey]);

  const reset = useCallback(() => {
    setWidth(allowZero ? 0 : defaultWidth);
    persist(allowZero ? 0 : defaultWidth);
  }, [allowZero, defaultWidth, persist]);

  // 拖动期间锁住文本选中与光标
  useEffect(() => {
    if (!dragging) return;
    const prev = document.body.style.userSelect;
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';
    return () => {
      document.body.style.userSelect = prev;
      document.body.style.cursor = '';
    };
  }, [dragging]);

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    const el = e.currentTarget as HTMLElement;
    el.setPointerCapture?.(e.pointerId);
    // 未设置过宽度（allowZero）时，以相邻兄弟元素的实际宽度为拖动起点
    let startW = width;
    if (allowZero && startW === 0) {
      const sibling = el.previousElementSibling as HTMLElement | null;
      startW = sibling?.offsetWidth
        || clamp(defaultWidth || min, min, max);
    }
    startRef.current = { x: e.clientX, w: startW };
    if (allowZero && width === 0) setWidth(startW);
    setDragging(true);
  }, [allowZero, defaultWidth, min, width]);

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    if (!dragging) return;
    const delta = e.clientX - startRef.current.x;
    setWidth(clamp(startRef.current.w + (invert ? -delta : delta), min, max));
  }, [dragging, invert, max, min]);

  const onPointerUp = useCallback(() => {
    if (!dragging) return;
    setDragging(false);
    persist(width);
  }, [dragging, persist, width]);

  const onKeyDown = useCallback((e: React.KeyboardEvent) => {
    const back = invert ? 'ArrowRight' : 'ArrowLeft';
    const fwd = invert ? 'ArrowLeft' : 'ArrowRight';
    if (e.key === back) {
      e.preventDefault();
      setWidth((w) => {
        const next = clamp(w - step, min, max);
        persist(next);
        return next;
      });
    } else if (e.key === fwd) {
      e.preventDefault();
      setWidth((w) => {
        const next = clamp(w + step, min, max);
        persist(next);
        return next;
      });
    }
  }, [invert, max, min, persist, step]);

  return {
    width,
    dragging,
    reset,
    handleProps: {
      onPointerDown,
      onPointerMove,
      onPointerUp,
      onPointerCancel: onPointerUp,
      onKeyDown,
      onDoubleClick: reset,
    },
  };
}
