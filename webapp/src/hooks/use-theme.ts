/**
 * 主题 hook（v1.5.0 三主题 + 重启轮换）：
 * - 三个配色主题：warm-paper（默认）/ graphite-lab / purple-gold，
 *   经 <html data-theme="..."> 生效（CSS token 在 index.css 定义）；
 * - 明暗模式沿用 .dark class（与主题正交）；
 * - 重启轮换：每次应用启动自动切到下一个主题（localStorage 存轮换序号）；
 *   用户在设置页手动选择后固定该主题（cof-scheme-manual 标记，停止轮换）。
 */
import { useCallback, useEffect, useState } from 'react';

const SCHEME_KEY = 'cof-scheme';
const SCHEME_MANUAL_KEY = 'cof-scheme-manual';
const SCHEME_INDEX_KEY = 'cof-scheme-index';
const MODE_KEY = 'cof-theme';

export type ColorScheme = 'warm-paper' | 'graphite-lab' | 'purple-gold';
export const COLOR_SCHEMES: ColorScheme[] = ['warm-paper', 'graphite-lab', 'purple-gold'];
export const SCHEME_LABELS: Record<ColorScheme, string> = {
  'warm-paper': '暖纸松石（默认）',
  'graphite-lab': '石墨仪器',
  'purple-gold': '学术紫金',
};

type Mode = 'light' | 'dark';

function getInitialMode(): Mode {
  const saved = localStorage.getItem(MODE_KEY);
  if (saved === 'light' || saved === 'dark') return saved;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

/** 模块级缓存：StrictMode 双渲染只推进一次轮换 */
let schemeResolved: ColorScheme | null = null;

function resolveInitialScheme(): ColorScheme {
  if (schemeResolved) return schemeResolved;
  let next: ColorScheme = 'warm-paper';
  try {
    const manual = localStorage.getItem(SCHEME_MANUAL_KEY);
    if (manual === '1') {
      const saved = localStorage.getItem(SCHEME_KEY);
      if (saved && (COLOR_SCHEMES as string[]).includes(saved)) {
        next = saved as ColorScheme;
      }
    } else {
      // 重启轮换：读上次序号 +1；首次运行（无序号）→ 默认 warm-paper
      const idxStr = localStorage.getItem(SCHEME_INDEX_KEY);
      const prev = idxStr ? parseInt(idxStr, 10) : -1;
      const idx = Number.isFinite(prev) ? (prev + 1) % COLOR_SCHEMES.length : 0;
      localStorage.setItem(SCHEME_INDEX_KEY, String(idx));
      next = COLOR_SCHEMES[idx];
    }
  } catch {
    next = 'warm-paper';
  }
  schemeResolved = next;
  return next;
}

export function useTheme() {
  const [mode, setMode] = useState<Mode>(getInitialMode);
  const [scheme, setSchemeState] = useState<ColorScheme>(resolveInitialScheme);

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle('dark', mode === 'dark');
    root.dataset.theme = scheme;
    localStorage.setItem(MODE_KEY, mode);
    localStorage.setItem(SCHEME_KEY, scheme);
  }, [mode, scheme]);

  const toggleTheme = useCallback(() => {
    setMode((m) => (m === 'dark' ? 'light' : 'dark'));
  }, []);

  /** 手动选择主题：固定该主题并停止重启轮换 */
  const setScheme = useCallback((s: ColorScheme) => {
    try {
      localStorage.setItem(SCHEME_MANUAL_KEY, '1');
      localStorage.setItem(SCHEME_INDEX_KEY, String(COLOR_SCHEMES.indexOf(s)));
    } catch {
      // localStorage 不可用时忽略，仅本次会话生效
    }
    setSchemeState(s);
  }, []);

  return { theme: mode, scheme, toggleTheme, setScheme };
}
