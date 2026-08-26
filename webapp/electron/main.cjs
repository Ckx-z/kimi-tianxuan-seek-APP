/**
 * COF 科研助手 — Electron 主进程（桌面壳）
 *
 * 启动流程：
 *   1. 选一个空闲端口（默认尝试 18765，被占则随机空闲端口；绝不使用 8000）
 *   2. spawn 后端：
 *      - dev 模式：配置的 Python 跑 `python -m uvicorn api.main:app --port <port>`
 *        （webapp/dist 已由 FastAPI 静态托管，前端无需单独 dev server）
 *      - prod 模式（app.isPackaged）：spawn 同目录 backend 可执行文件（本波次先用
 *        python 占位，切换逻辑见 resolveBackendCommand）
 *   3. 轮询 /api/health 通过（并记录后端版本）后创建 BrowserWindow 加载
 *      http://localhost:<port>/
 *   4. 窗口/应用退出时杀掉后端子进程（Windows 用 taskkill /T 杀整棵进程树，
 *      只杀自己 spawn 的 PID，不碰其它用户进程）
 *
 * 版本健壮性（2026-08-26 根治包）：
 *   - 运行标记 userData/.running-instance {pid, version, started_at}
 *   - 版本看门狗：新实例拿不到单实例锁时，若运行中实例是旧版本（更新安装时
 *     旧进程没被杀掉的残留场景），主动结束旧实例进程树、延迟 1s 重新拿锁并
 *     继续自身启动——不再"退出并聚焦旧窗口"让用户看到旧版界面
 *   - 后端版本握手：health 返回的 version 与 app.getVersion() 不一致时，
 *     主窗口加载完成后向渲染进程发 'backend-version-mismatch' 事件
 */

const { app, BrowserWindow, dialog, ipcMain, shell } = require('electron');
const { spawn, execSync } = require('child_process');
const net = require('net');
const http = require('http');
const path = require('path');
const fs = require('fs');

// webapp/electron/main.cjs -> 项目根 = ../../
const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const ICON_PATH = path.join(PROJECT_ROOT, 'assets', 'app_icon.ico');
const PREFERRED_PORT = 18765;

let backendProc = null;
let backendPort = null;
let mainWindow = null;
/** 后端 /api/health 返回的版本号（旧后端无该字段时为 null） */
let backendVersion = null;

// ── 版本变更自清缓存（2026-08-24 二次缓存事故根治）─────────────
// 背景：旧版后端无 Cache-Control 时 Chromium 启发式缓存会缓存旧页面；
// 仅靠服务端 no-cache 头无法救"已被旧版污染的缓存"（v0.3.0→v1.0.0 即复发）。
// 因此每次检测到应用版本变化，主动清空磁盘缓存——旧版本留下的毒缓存
// 也能在新版首次启动时自愈。
async function clearCacheOnVersionChange() {
  try {
    const marker = path.join(app.getPath('userData'), '.app-version');
    const current = app.getVersion();
    let previous = null;
    try { previous = fs.readFileSync(marker, 'utf-8').trim(); } catch (e) { /* 首次运行 */ }
    if (previous !== current) {
      const { session } = require('electron');
      await session.defaultSession.clearCache();
      await session.defaultSession.clearCodeCaches({});
      fs.writeFileSync(marker, current, 'utf-8');
      console.log(`[electron] 版本变更 ${previous || '(首次)'} -> ${current}，已清空页面缓存`);
    }
  } catch (e) {
    console.warn('[electron] 版本变更清缓存失败（不影响启动）:', e.message);
  }
}

// ── 运行标记文件（.running-instance）─────────────────────────────
// 记录当前持有单实例锁进程的 {pid, version, started_at}，供：
//  1) 版本看门狗判断"运行中的实例是不是旧版本"；
//  2) 启动时识别僵死标记（pid 已死直接覆盖）。
function instanceMarkerPath() {
  try {
    return path.join(app.getPath('userData'), '.running-instance');
  } catch (e) {
    return null; // userData 路径不可用时降级（不影响启动）
  }
}

function readInstanceMarker() {
  try {
    const p = instanceMarkerPath();
    if (!p) return null;
    return JSON.parse(fs.readFileSync(p, 'utf-8'));
  } catch (e) {
    return null; // 标记不存在或已损坏：按无标记处理
  }
}

function writeInstanceMarker() {
  try {
    const p = instanceMarkerPath();
    if (!p) return;
    fs.writeFileSync(p, JSON.stringify({
      pid: process.pid,
      version: app.getVersion(),
      started_at: new Date().toISOString(),
    }), 'utf-8');
  } catch (e) {
    console.warn('[electron] 写运行标记失败（不影响启动）:', e.message);
  }
}

/** 正常退出时删标记；仅当标记里的 pid 是自己时才删，防误删新实例标记 */
function removeInstanceMarkerIfMine() {
  try {
    const p = instanceMarkerPath();
    if (!p) return;
    const m = JSON.parse(fs.readFileSync(p, 'utf-8'));
    if (m && m.pid === process.pid) fs.unlinkSync(p);
  } catch (e) { /* 标记不存在或已损坏：忽略 */ }
}

/** 跨平台探测进程是否存活（signal 0 不发信号只检查存在性） */
function isPidAlive(pid) {
  if (!pid || typeof pid !== 'number') return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return e && e.code === 'EPERM'; // 存在但无权发信号也算活着
  }
}

/** 强制结束指定进程整棵进程树（结束旧版本实例用，全程容错） */
function killProcessTree(pid) {
  try {
    if (process.platform === 'win32') {
      // /T 连同其后端子进程（cof-backend.exe / uvicorn）一起结束
      execSync(`taskkill /PID ${pid} /T /F`, { stdio: 'ignore' });
    } else {
      process.kill(pid, 'SIGKILL');
    }
  } catch (e) {
    console.warn(`[electron] 结束旧实例进程 pid=${pid} 失败（可能已退出）:`, e.message);
  }
}

// ── 防多开：单实例锁 + 版本看门狗 ────────────────────────────────
// 常规情况：连续双击桌面图标时，第二个实例直接退出；已有实例收到
// second-instance 事件后 restore + focus 主窗口。
//
// 版本看门狗（2026-08-26）：新实例拿不到锁时先读运行标记——若运行中
// 实例版本 ≠ 自己（典型场景：更新安装时旧进程未被杀，装完用户双击，
// 按旧逻辑会"退出并聚焦旧窗口"，用户永远看到旧版界面），则由新实例
// 主动结束旧实例进程树，延迟 1s 等其退出后重新拿锁、继续自身启动。
function registerSecondInstanceHandler() {
  app.on('second-instance', () => {
    // 能走到这里说明第二实例已按上面逻辑判定为同版本并自行退出，
    // 本实例只需把窗口唤到前台
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
    }
  });
}

let gotSingleInstanceLock = app.requestSingleInstanceLock();

if (!gotSingleInstanceLock) {
  const marker = readInstanceMarker();
  const myVersion = app.getVersion();
  if (marker && marker.version && marker.version !== myVersion && isPidAlive(marker.pid)) {
    console.log(
      `[electron] 版本看门狗：运行中实例为 v${marker.version} (pid=${marker.pid})，` +
      `本实例为 v${myVersion}，结束旧实例并接管启动`
    );
    killProcessTree(marker.pid);
    // 等旧实例（及其后端进程树）退出后再重新拿锁
    setTimeout(() => {
      gotSingleInstanceLock = app.requestSingleInstanceLock();
      if (gotSingleInstanceLock) {
        registerSecondInstanceHandler();
        app.whenReady().then(startApp);
      } else {
        console.log('[electron] 重新获取单实例锁失败，本实例退出');
        app.quit();
      }
    }, 1000);
  } else {
    console.log('[electron] 已有实例在运行，本实例退出');
    app.quit();
  }
} else {
  registerSecondInstanceHandler();
}

// ── 自动更新（electron-updater + GitHub Releases）────────────
// 检查 GitHub Releases（Ckx-z/kimi-tianxuan-seek-APP）。
// - 启动时静默检查（仅打包后）：发现新版本弹系统对话框，确认后下载，
//   下载完成提示重启安装。
// - 设置页手动检查：渲染进程经 preload（window.updater）调
//   check-for-updates / download-update / install-update IPC，
//   各阶段状态（检查中/已是最新/发现新版/下载进度/下载完成/错误）
//   通过 updater:status 事件推给渲染进程，由设置页内 UI 承接（不弹对话框）。

/** 手动检查进行中：该轮事件不弹系统对话框，交给设置页 UI 处理 */
let manualCheckInFlight = false;
/** 防止重复点击触发并发检查 */
let updateCheckRunning = false;

/** 把更新状态推给渲染进程（窗口可能尚未创建或已销毁，需判空） */
function sendUpdaterStatus(payload) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('updater:status', payload);
  }
}

/** 把底层错误翻成用户友好的提示语 */
function friendlyUpdateError(e) {
  const msg = String((e && e.message) || e || '');
  if (/app-update\.yml|ENOENT/i.test(msg)) return '更新组件缺失，请重新安装最新版本';
  if (/ERR_INTERNET_DISCONNECTED|ENOTFOUND|ETIMEDOUT|ECONNREFUSED|ECONNRESET|network|getaddrinfo/i.test(msg))
    return '网络连接失败，请检查网络后重试';
  if (/404|not found/i.test(msg)) return '暂未找到更新服务（可能尚未发布新版本）';
  return '检查更新失败，请稍后重试';
}

function setupAutoUpdater() {
  // IPC 与 dev/prod 无关，先注册，dev 下也能给出友好答复
  ipcMain.handle('get-app-version', () => app.getVersion());

  let autoUpdater = null;
  try {
    ({ autoUpdater } = require('electron-updater'));
  } catch (e) {
    console.warn('[electron] electron-updater 不可用:', e.message);
  }

  ipcMain.handle('check-for-updates', async () => {
    if (!app.isPackaged) {
      return { ok: false, state: 'dev', message: '开发模式不支持更新检查，仅打包后的桌面版可用' };
    }
    if (!autoUpdater) {
      return { ok: false, state: 'error', message: '更新组件不可用' };
    }
    if (updateCheckRunning) {
      return { ok: true, state: 'checking', currentVersion: app.getVersion() };
    }
    updateCheckRunning = true;
    manualCheckInFlight = true;
    sendUpdaterStatus({ state: 'checking' });
    try {
      const result = await autoUpdater.checkForUpdates();
      // 结果细节由事件流推送；这里返回即时摘要
      return {
        ok: true,
        state: 'done',
        currentVersion: app.getVersion(),
        latestVersion: result && result.updateInfo ? result.updateInfo.version : undefined,
      };
    } catch (e) {
      manualCheckInFlight = false;
      const message = friendlyUpdateError(e);
      sendUpdaterStatus({ state: 'error', message });
      return { ok: false, state: 'error', message };
    } finally {
      updateCheckRunning = false;
    }
  });

  ipcMain.handle('download-update', () => {
    if (autoUpdater) autoUpdater.downloadUpdate();
  });

  ipcMain.handle('install-update', () => {
    if (autoUpdater) autoUpdater.quitAndInstall();
  });

  if (!autoUpdater) return;

  autoUpdater.autoDownload = false; // 用户确认后再下载

  autoUpdater.on('update-available', (info) => {
    sendUpdaterStatus({ state: 'available', version: info.version });
    if (manualCheckInFlight) return; // 手动检查：设置页内 UI 承接确认
    dialog.showMessageBox(mainWindow, {
      type: 'info',
      title: '发现新版本',
      message: `发现新版本 v${info.version}`,
      detail: '是否立即下载更新？下载完成后会提示重启安装。',
      buttons: ['立即下载', '稍后'],
      defaultId: 0,
      cancelId: 1,
    }).then(({ response }) => {
      if (response === 0) autoUpdater.downloadUpdate();
    }).catch(() => {});
  });

  autoUpdater.on('update-not-available', (info) => {
    manualCheckInFlight = false;
    sendUpdaterStatus({ state: 'latest', version: info && info.version });
  });

  autoUpdater.on('download-progress', (p) => {
    sendUpdaterStatus({ state: 'downloading', percent: Math.round(p.percent || 0) });
  });

  autoUpdater.on('update-downloaded', (info) => {
    sendUpdaterStatus({ state: 'downloaded', version: info.version });
    if (manualCheckInFlight) {
      manualCheckInFlight = false;
      return; // 手动检查：设置页内显示“重启安装”按钮
    }
    dialog.showMessageBox(mainWindow, {
      type: 'info',
      title: '更新已就绪',
      message: `新版本 v${info.version} 已下载完成`,
      detail: '重启应用以完成安装。是否现在重启？',
      buttons: ['重启安装', '稍后'],
      defaultId: 0,
      cancelId: 1,
    }).then(({ response }) => {
      if (response === 0) autoUpdater.quitAndInstall();
    }).catch(() => {});
  });

  autoUpdater.on('error', (e) => {
    manualCheckInFlight = false;
    sendUpdaterStatus({ state: 'error', message: friendlyUpdateError(e) });
    console.warn('[electron] 自动更新检查失败:', e.message || e);
  });

  // 启动时的静默检查仅打包后执行
  if (!app.isPackaged) {
    console.log('[electron] dev 模式，跳过启动时的自动更新检查');
    return;
  }
  updateCheckRunning = true;
  autoUpdater.checkForUpdates()
    .catch((e) => {
      // 未发布 Release / 网络问题时静默记录，不打扰用户
      console.warn('[electron] 自动更新检查失败:', e.message || e);
    })
    .finally(() => { updateCheckRunning = false; });
}

/** dev: 返回 python 命令；prod: 优先同目录 backend exe，缺失时回退 python 占位 */
function resolveBackendCommand(port) {
  const python = process.env.COF_PYTHON || 'E:\\ANACONDA\\python.exe';
  if (app.isPackaged) {
    // 下一波次：PyInstaller 产物放 resources 同目录，例如 cof-backend.exe --port <port>
    const exe = path.join(process.resourcesPath || PROJECT_ROOT, 'backend',
      process.platform === 'win32' ? 'cof-backend.exe' : 'cof-backend');
    if (fs.existsSync(exe)) {
      return { cmd: exe, args: ['--port', String(port)], cwd: PROJECT_ROOT };
    }
    console.warn('[electron] backend exe 未找到，回退 python 占位:', exe);
  }
  return {
    cmd: python,
    args: ['-m', 'uvicorn', 'api.main:app', '--host', '127.0.0.1', '--port', String(port)],
    cwd: PROJECT_ROOT,
  };
}

function isPortFree(port) {
  return new Promise((resolve) => {
    const srv = net.createServer();
    srv.once('error', () => resolve(false));
    srv.once('listening', () => srv.close(() => resolve(true)));
    srv.listen(port, '127.0.0.1');
  });
}

function pickFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

async function choosePort() {
  if (await isPortFree(PREFERRED_PORT)) return PREFERRED_PORT;
  console.warn(`[electron] 端口 ${PREFERRED_PORT} 被占，改用随机空闲端口`);
  return pickFreePort();
}

/**
 * 轮询 /api/health 直到就绪；resolve { version }（版本握手用，
 * 旧后端无 version 字段时 version 为 null，不算不一致）。
 */
function waitForHealth(port, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const req = http.get(
        { host: '127.0.0.1', port, path: '/api/health', timeout: 2000 },
        (res) => {
          if (res.statusCode !== 200) {
            res.resume();
            return retry();
          }
          let body = '';
          res.setEncoding('utf-8');
          res.on('data', (c) => { body += c; });
          res.on('end', () => {
            let version = null;
            try {
              const data = JSON.parse(body);
              version = (data && typeof data.version === 'string') ? data.version : null;
            } catch (e) { /* 非 JSON 响应：视为无版本信息 */ }
            resolve({ version });
          });
        }
      );
      req.on('error', retry);
      req.on('timeout', () => { req.destroy(); retry(); });
    };
    const retry = () => {
      if (backendProc && backendProc.exitCode !== null) {
        return reject(new Error(`后端进程已退出，exitCode=${backendProc.exitCode}`));
      }
      if (Date.now() > deadline) return reject(new Error('等待后端健康检查超时'));
      setTimeout(attempt, 500);
    };
    attempt();
  });
}

function killBackend() {
  if (!backendProc || backendProc.exitCode !== null) return;
  const pid = backendProc.pid;
  try {
    if (process.platform === 'win32') {
      // 只杀本进程 spawn 的子进程树（uvicorn 可能再 fork reload/worker 子进程）
      execSync(`taskkill /PID ${pid} /T /F`, { stdio: 'ignore' });
    } else {
      backendProc.kill('SIGTERM');
    }
  } catch (e) {
    console.warn('[electron] 杀后端进程失败（可能已退出）:', e.message);
  }
  backendProc = null;
}

async function startBackend() {
  backendPort = await choosePort();
  const { cmd, args, cwd } = resolveBackendCommand(backendPort);
  console.log(`[electron] 启动后端: ${cmd} ${args.join(' ')} (cwd=${cwd})`);
  backendProc = spawn(cmd, args, { cwd, stdio: ['ignore', 'pipe', 'pipe'] });
  backendProc.stdout.on('data', (d) => process.stdout.write(`[backend] ${d}`));
  backendProc.stderr.on('data', (d) => process.stderr.write(`[backend] ${d}`));
  backendProc.on('exit', (code) => console.log(`[electron] 后端退出 code=${code}`));
  const health = await waitForHealth(backendPort);
  backendVersion = health.version;
  console.log(`[electron] 后端就绪: http://localhost:${backendPort}/ (version=${backendVersion || '未知'})`);
  if (backendVersion && backendVersion !== app.getVersion()) {
    // 极端情况：连上的不是自己这套后端（如旧后端进程残留占用并应答）。
    // 不阻断启动，窗口加载完成后通知渲染进程显示红色横幅。
    console.warn(
      `[electron] 后端版本 v${backendVersion} 与界面版本 v${app.getVersion()} 不一致，` +
      '将提示用户完全退出后重开'
    );
  }
}

/** 仅允许 http/https 外链（拦截 file:/javascript: 等危险协议） */
function isExternalHttpUrl(url) {
  try {
    const u = new URL(url);
    return u.protocol === 'http:' || u.protocol === 'https:';
  } catch {
    return false;
  }
}

// 渲染进程经 preload（window.shell.openExternal）请求系统浏览器打开外链
ipcMain.handle('open-external', (_event, url) => {
  if (!isExternalHttpUrl(url)) {
    console.warn('[electron] 拒绝打开非 http(s) 链接:', url);
    return false;
  }
  return shell.openExternal(url).then(() => true).catch((e) => {
    console.warn('[electron] openExternal 失败:', e);
    return false;
  });
});

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    title: 'COF 科研助手',
    icon: fs.existsSync(ICON_PATH) ? ICON_PATH : undefined,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.loadURL(`http://localhost:${backendPort}/`);
  mainWindow.on('closed', () => { mainWindow = null; });

  // 后端版本握手：页面加载完成后若版本不一致，通知渲染进程显示横幅
  mainWindow.webContents.on('did-finish-load', () => {
    if (backendVersion && backendVersion !== app.getVersion()
        && mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('backend-version-mismatch', {
        backendVersion,
        appVersion: app.getVersion(),
      });
    }
  });

  // ── 外部链接（DOI 等）：不在应用窗口内打开，交系统浏览器 ──
  // target=_blank / window.open：拦截并转 shell.openExternal
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (isExternalHttpUrl(url)) {
      shell.openExternal(url).catch((e) => console.warn('[electron] openExternal 失败:', e));
      return { action: 'deny' };
    }
    return { action: 'deny' }; // 应用内一律单窗口，不允许弹新窗口
  });
  // 意外导航到外部 origin（如误点无 target 的外链）：拦下并转系统浏览器
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (isExternalHttpUrl(url) && url !== `http://localhost:${backendPort}/`
        && !url.startsWith(`http://localhost:${backendPort}/`)) {
      event.preventDefault();
      shell.openExternal(url).catch((e) => console.warn('[electron] openExternal 失败:', e));
    }
  });
}

/** 应用启动主流程（首实例与看门狗接管后的新实例共用，防重入） */
let appStarted = false;
async function startApp() {
  if (appStarted) return;
  appStarted = true;
  try {
    await clearCacheOnVersionChange();
    writeInstanceMarker(); // 覆盖旧标记（含僵死标记：旧 pid 已死也直接覆盖）
    await startBackend();
    createWindow();
    setupAutoUpdater();
  } catch (e) {
    console.error('[electron] 启动失败:', e);
    killBackend();
    app.exit(1);
  }
}

app.whenReady().then(() => {
  // 未拿到单实例锁的第二实例：同版本走 app.quit() 路径；看门狗接管路径
  // 会在拿到锁后自行调用 startApp，这里统一由 gotSingleInstanceLock 守门
  if (!gotSingleInstanceLock) return;
  startApp();
});

app.on('window-all-closed', () => {
  killBackend();
  app.quit();
});

app.on('before-quit', killBackend);
process.on('exit', () => {
  killBackend();
  removeInstanceMarkerIfMine();
});
