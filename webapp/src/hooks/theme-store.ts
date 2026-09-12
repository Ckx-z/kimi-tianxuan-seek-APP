/**
 * 主题单一数据源（v1.9.4 修复 bug：来回切换主题/明暗导致「选择的主题与实际不匹配」）。
 *
 * 原实现的问题：`useTheme()` 在 Settings（配色主题卡）与 AppLayout（侧栏明暗开关）
 * 各调用一次 → **两个互不相识的 useState**，却都往同一批 localStorage key 与
 * `<html data-theme>` / `.dark` 上写。于是：
 *   设置页选「暖纸松石」→ attr=warm-paper；
 *   侧栏切一次明暗 → 侧栏那份**过期 state**（旧主题）重新写回 → attr 被打回旧主题，
 *   而设置页仍显示新选择 → 「主题和选择的主题不匹配」。
 *
 * 现在：模块级 store + `useSyncExternalStore`，所有调用方共享同一份状态，
 * 任何一次变更只应用一次（写 DOM + localStorage），不存在互相覆盖。
 */
import { useSyncExternalStore } from 'react';

export type ColorScheme = 'warm-paper' | 'graphite-lab' | 'purple-gold';
export type Mode = 'light' | 'dark';

export const COLOR_SCHEMES: ColorScheme[] = ['warm-paper', 'graphite-lab', 'purple-gold'];
export const SCHEME_LABELS: Record<ColorScheme, string> = {
  'warm-paper': '暖纸松石（默认）',
  'graphite-lab': '石墨仪器',
  'purple-gold': '学术紫金',
};

const SCHEME_KEY = 'cof-scheme';
const SCHEME_MANUAL_KEY = 'cof-scheme-manual';
const SCHEME_INDEX_KEY = 'cof-scheme-index';
const MODE_KEY = 'cof-theme';

interface ThemeState {
  mode: Mode;
  scheme: ColorScheme;
}

function readMode(): Mode {
  try {
    const saved = localStorage.getItem(MODE_KEY);
    if (saved === 'light' || saved === 'dark') return saved;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

function readScheme(): ColorScheme {
  try {
    const manual = localStorage.getItem(SCHEME_MANUAL_KEY);
    if (manual === '1') {
      const saved = localStorage.getItem(SCHEME_KEY);
      if (saved && (COLOR_SCHEMES as string[]).includes(saved)) {
        return saved as ColorScheme;
      }
    }
    // 重启轮换：读上次序号 +1；首次运行 → 默认 warm-paper
    const idxStr = localStorage.getItem(SCHEME_INDEX_KEY);
    const prev = idxStr ? parseInt(idxStr, 10) : -1;
    const idx = Number.isFinite(prev) ? (prev + 1) % COLOR_SCHEMES.length : 0;
    localStorage.setItem(SCHEME_INDEX_KEY, String(idx));
    return COLOR_SCHEMES[idx];
  } catch {
    return 'warm-paper';
  }
}

/** 单例状态（模块级，StrictMode 双渲染 / 多组件调用都只有一份） */
let state: ThemeState = { mode: readMode(), scheme: readScheme() };
const listeners = new Set<() => void>();

function apply(next: ThemeState) {
  const root = document.documentElement;
  root.classList.toggle('dark', next.mode === 'dark');
  root.dataset.theme = next.scheme;
  try {
    localStorage.setItem(MODE_KEY, next.mode);
    localStorage.setItem(SCHEME_KEY, next.scheme);
  } catch {
    /* localStorage 不可用：仅本次会话生效 */
  }
}

/** 首次加载立即把状态落到 DOM（避免首帧闪主题） */
apply(state);

function setState(patch: Partial<ThemeState>) {
  const next = { ...state, ...patch };
  if (next.mode === state.mode && next.scheme === state.scheme) return;
  state = next;
  apply(state);
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot(): ThemeState {
  return state;
}

export const themeStore = {
  subscribe,
  getSnapshot,
  toggleMode() {
    setState({ mode: state.mode === 'dark' ? 'light' : 'dark' });
  },
  setMode(mode: Mode) {
    setState({ mode });
  },
  /** 手动选择主题：固定该主题并停止重启轮换 */
  setScheme(scheme: ColorScheme) {
    try {
      localStorage.setItem(SCHEME_MANUAL_KEY, '1');
      localStorage.setItem(SCHEME_INDEX_KEY, String(COLOR_SCHEMES.indexOf(scheme)));
    } catch {
      /* ignore */
    }
    setState({ scheme });
  },
};

/** 共享主题状态（所有组件拿到的是同一份） */
export function useThemeState(): ThemeState {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
