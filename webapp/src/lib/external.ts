/**
 * 外部链接打开辅助（DOI 等）。
 * - Electron 桌面壳：经 preload 暴露的 window.shell.openExternal 交主进程
 *   shell.openExternal，用系统浏览器打开（不在应用窗口内导航）；
 * - 浏览器/dev 环境：window.open 新标签页兜底（noopener/noreferrer）。
 * 非 http(s) 协议一律拒绝。
 */
export function openExternal(url: string): void {
  if (!/^https?:\/\//i.test(url)) return;
  const shellApi = (window as unknown as {
    shell?: { openExternal?: (u: string) => Promise<boolean> };
  }).shell;
  if (shellApi?.openExternal) {
    void shellApi.openExternal(url);
    return;
  }
  window.open(url, '_blank', 'noopener,noreferrer');
}

/** 归一化 DOI 为可点击链接；无 DOI 返回 null */
export function doiUrl(doi?: string | null): string | null {
  const d = (doi ?? '').trim().replace(/^https?:\/\/(dx\.)?doi\.org\//i, '');
  return d ? `https://doi.org/${d}` : null;
}
