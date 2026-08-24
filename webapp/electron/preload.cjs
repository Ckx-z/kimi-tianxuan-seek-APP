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
 *
 * 状态事件 payload：
 *   { state: 'checking' }
 *   { state: 'latest',     version? }   已是最新
 *   { state: 'available',  version }    发现新版本
 *   { state: 'downloading', percent }   下载进度 0-100
 *   { state: 'downloaded', version }    下载完成，可重启安装
 *   { state: 'error',      message }    友好错误提示
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
});
