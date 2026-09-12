/**
 * 界面密度（v1.9.3 UI）：舒适 / 紧凑两档，写 localStorage 并通过
 * `<html data-density="...">` 生效（CSS 变量在 index.css 定义）。
 *
 * 设计取舍：不改组件结构，只让「数据密集区域」消费密度变量
 * （`--density-card-p` 卡片内边距、`--density-cell-y` 表格单元格纵向内边距），
 * 因此对布局与功能零影响，且可随时回退。
 */
import { useCallback, useEffect, useState } from 'react';

export type Density = 'comfortable' | 'compact';

export const DENSITIES: Density[] = ['comfortable', 'compact'];
export const DENSITY_LABELS: Record<Density, string> = {
  comfortable: '舒适',
  compact: '紧凑',
};
export const DENSITY_HINTS: Record<Density, string> = {
  comfortable: '默认：留白充足，适合逐条阅读',
  compact: '行高与内边距收紧，同屏看到更多记录',
};

const STORAGE_KEY = 'cof.appearance.density';
const EVENT_KEY = 'cof:density-changed';

function resolveInitial(): Density {
  if (typeof window === 'undefined') return 'comfortable';
  const saved = window.localStorage.getItem(STORAGE_KEY);
  return saved === 'compact' ? 'compact' : 'comfortable';
}

function apply(density: Density) {
  if (typeof document === 'undefined') return;
  document.documentElement.setAttribute('data-density', density);
}

export function useDensity() {
  const [density, setDensityState] = useState<Density>(resolveInitial);

  useEffect(() => {
    apply(density);
  }, [density]);

  // 跨组件同步：布局（常驻）与设置页（切换入口）各自持有 hook 实例，
  // 切换时广播事件，避免一个实例改了、另一个实例的状态还是旧值。
  useEffect(() => {
    const onChange = (e: Event) => {
      const next = (e as CustomEvent<Density>).detail;
      if (next === 'compact' || next === 'comfortable') setDensityState(next);
    };
    window.addEventListener(EVENT_KEY, onChange);
    return () => window.removeEventListener(EVENT_KEY, onChange);
  }, []);

  const setDensity = useCallback((next: Density) => {
    setDensityState(next);
    apply(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* 隐私模式等写入失败忽略 */
    }
    window.dispatchEvent(new CustomEvent(EVENT_KEY, { detail: next }));
  }, []);

  return { density, setDensity };
}
