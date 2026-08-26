/**
 * COF 科研助手 — Electron preload
 *
 * 通过 contextBridge 向渲染进程暴露最小化 API（保持 contextIsolation 开启，
 * 不启用 nodeIntegration）：
 *
 *   window.updater.getVersion()        -> Promise<string>  当前应用版本
 *   window.updater.check()             -> Promise<{ ok, state, message?, currentVersion?, latestVersion? }>
 *   window.updater.download()          -> Promise<void>    开始下载已发现的更新
 *   window.updater.install()           -> Promise<void>    重启并安装已下载的更新
 *   window.updater.onStatus(cb)        -> () => void       订阅更新状态，返回取消订阅函数
 *   window.updater.onBackendVersionMismatch(cb)
 *                                      -> () => void       订阅后端/界面版本不一致事件
 *   window.shell.openExternal(url)     -> Promise<boolean> 系统浏览器打开 http(s) 外链
 *
 * 状态事件 payload：
 *   { state: 'checking' }
 *   { state: 'latest',     version? }   已是最新
 *   { state: 'available',  version }    发现新版本
 *   { state: 'downloading', percent }   下载进度 0-100
 *   { state: 'downloaded', version }    下载完成，可重启安装
 *   { state: 'error',      message }    友好错误提示
 *
 * 版本不一致事件 payload（2026-08-26 后端版本握手）：
 *   { backendVersion: string, appVersion: string }
 *   主进程检测到后端 version ≠ 应用版本时推送，前端应显示红色横幅。
 */

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('updater', {
  getVersion: () => ipcRenderer.invoke('get-app-version'),
  check: () => ipcRenderer.invoke('check-for-updates'),
  download: () => ipcRenderer.invoke('download-update'),
  install: () => ipcRenderer.invoke('install-update'),
  onStatus: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on('updater:status', listener);
    return () => ipcRenderer.removeListener('updater:status', listener);
  },
  // 后端版本握手：后端 version 与应用版本不一致时主进程推送
  onBackendVersionMismatch: (callback) => {
    const listener = (_event, payload) => callback(payload);
    ipcRenderer.on('backend-version-mismatch', listener);
    return () => ipcRenderer.removeListener('backend-version-mismatch', listener);
  },
});

// 外部链接（DOI 等）：交主进程 shell.openExternal 用系统浏览器打开，
// 不在应用窗口内导航。仅允许 http/https（主进程侧再校验一次）。
contextBridge.exposeInMainWorld('shell', {
  openExternal: (url) => ipcRenderer.invoke('open-external', url),
});
