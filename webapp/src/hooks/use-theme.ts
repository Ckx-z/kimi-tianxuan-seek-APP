/**
 * 主题 hook（v1.9.4 重构为共享 store）：
 * - 三个配色主题：warm-paper（默认）/ graphite-lab / purple-gold，经
 *   `<html data-theme="...">` 生效（CSS token 在 index.css 定义）；
 * - 明暗模式沿用 `.dark` class（与配色主题正交）；
 * - 重启轮换：每次应用启动自动切到下一个主题（localStorage 存轮换序号）；
 *   用户在设置页手动选择后固定该主题（cof-scheme-manual 标记，停止轮换）。
 *
 * 兼容层：保留原 API（theme / scheme / toggleTheme / setScheme），
 * 但底层改为 `theme-store` 的**单一数据源** —— 之前 Settings 与 AppLayout
 * 各持一份 useState，切明暗时侧栏会用过期主题覆盖设置页的选择（已修）。
 */
import { useCallback } from 'react';

import {
  COLOR_SCHEMES, SCHEME_LABELS, themeStore, useThemeState,
  type ColorScheme, type Mode,
} from '@/hooks/theme-store';

export { COLOR_SCHEMES, SCHEME_LABELS };
export type { ColorScheme, Mode };

export function useTheme() {
  const { mode, scheme } = useThemeState();

  const toggleTheme = useCallback(() => {
    themeStore.toggleMode();
  }, []);

  const setScheme = useCallback((s: ColorScheme) => {
    themeStore.setScheme(s);
  }, []);

  return { theme: mode, scheme, toggleTheme, setScheme };
}
