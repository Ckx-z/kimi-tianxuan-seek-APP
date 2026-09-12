/**
 * 界面字体风格（v1.9.4）：无衬线（默认，现代清晰）/ 宋体·Times（经典学术）。
 *
 * 实现：写 localStorage + `<html data-font="sans|serif">`，由 index.css 里的
 * `--font-body` / `--font-display` 变量切换；正文与页面主标题分别取值，
 * 因此「正文无衬线 + 标题衬线」这种组合也能保持。
 */
import { useCallback, useEffect, useState } from 'react';

export type FontStyle = 'sans' | 'serif';

export const FONT_STYLES: FontStyle[] = ['sans', 'serif'];
export const FONT_LABELS: Record<FontStyle, string> = {
  sans: '无衬线（现代清晰，推荐）',
  serif: '宋体 / Times（经典学术）',
};
export const FONT_HINTS: Record<FontStyle, string> = {
  sans: '正文与界面用无衬线（微软雅黑/PingFang + 系统 UI 字体），小字号更清晰',
  serif: '正文回到宋体 + Times New Roman，书卷气更足（小字号略发虚）',
};

const STORAGE_KEY = 'cof.appearance.font';
const EVENT_KEY = 'cof:font-changed';

function resolveInitial(): FontStyle {
  if (typeof window === 'undefined') return 'sans';
  return window.localStorage.getItem(STORAGE_KEY) === 'serif' ? 'serif' : 'sans';
}

function apply(style: FontStyle) {
  if (typeof document === 'undefined') return;
  document.documentElement.setAttribute('data-font', style);
}

export function useFontStyle() {
  const [fontStyle, setFontStyleState] = useState<FontStyle>(resolveInitial);

  useEffect(() => {
    apply(fontStyle);
  }, [fontStyle]);

  useEffect(() => {
    const onChange = (e: Event) => {
      const next = (e as CustomEvent<FontStyle>).detail;
      if (next === 'sans' || next === 'serif') setFontStyleState(next);
    };
    window.addEventListener(EVENT_KEY, onChange);
    return () => window.removeEventListener(EVENT_KEY, onChange);
  }, []);

  const setFontStyle = useCallback((next: FontStyle) => {
    setFontStyleState(next);
    apply(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* 隐私模式等写入失败忽略 */
    }
    window.dispatchEvent(new CustomEvent(EVENT_KEY, { detail: next }));
  }, []);

  return { fontStyle, setFontStyle };
}
