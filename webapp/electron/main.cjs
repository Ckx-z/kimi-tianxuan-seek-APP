/**
 * COF 成膜推荐系统 — Electron 主进程（桌面壳）
 *
 * 启动流程：
 *   1. 选一个空闲端口（默认尝试 18765，被占则随机空闲端口；绝不使用 8000）
 *   2. spawn 后端：
 *      - dev 模式：配置的 Python 跑 `python -m uvicorn api.main:app --port <port>`
 *        （webapp/dist 已由 FastAPI 静态托管，前端无需单独 dev server）
 *      - prod 模式（app.isPackaged）：spawn 同目录 backend 可执行文件（本波次先用
 *        python 占位，切换逻辑见 resolveBackendCommand）
 *   3. 轮询 /api/health 通过后创建 BrowserWindow 加载 http://localhost:<port>/
 *   4. 窗口/应用退出时杀掉后端子进程（Windows 用 taskkill /T 杀整棵进程树，
 *      只杀自己 spawn 的 PID，不碰其它用户进程）
 */

const { app, BrowserWindow, dialog } = require('electron');
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

// ── 防多开：单实例锁 ─────────────────────────────────────────
// 连续双击桌面图标时，第二个实例直接退出；已有实例收到
// second-instance 事件后 restore + focus 主窗口。
const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  console.log('[electron] 已有实例在运行，本实例退出');
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
    }
  });
}

// ── 自动更新（electron-updater + GitHub Releases）────────────
// 仅打包后生效；检查 GitHub Releases（Ckx-z/kimi-tianxuan-seek-APP），
// 有新版本时弹窗询问，确认后下载，下载完成提示重启安装。
function setupAutoUpdater() {
  if (!app.isPackaged) {
    console.log('[electron] dev 模式，跳过自动更新检查');
    return;
  }
  let autoUpdater;
  try {
    ({ autoUpdater } = require('electron-updater'));
  } catch (e) {
    console.warn('[electron] electron-updater 不可用，跳过自动更新:', e.message);
    return;
  }
  autoUpdater.autoDownload = false; // 用户确认后再下载
  autoUpdater.on('update-available', (info) => {
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
  autoUpdater.on('update-downloaded', (info) => {
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
    // 未发布 Release / 网络问题时静默记录，不打扰用户
    console.warn('[electron] 自动更新检查失败:', e.message || e);
  });
  autoUpdater.checkForUpdates().catch((e) => {
    console.warn('[electron] 自动更新检查失败:', e.message || e);
  });
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

function waitForHealth(port, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const req = http.get(
        { host: '127.0.0.1', port, path: '/api/health', timeout: 2000 },
        (res) => {
          res.resume();
          if (res.statusCode === 200) return resolve();
          retry();
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
  await waitForHealth(backendPort);
  console.log(`[electron] 后端就绪: http://localhost:${backendPort}/`);
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    title: 'COF 成膜推荐系统',
    icon: fs.existsSync(ICON_PATH) ? ICON_PATH : undefined,
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.loadURL(`http://localhost:${backendPort}/`);
  mainWindow.on('closed', () => { mainWindow = null; });
}

app.whenReady().then(async () => {
  // 未拿到单实例锁的第二实例已在 app.quit() 路径上，不再启动后端
  if (!gotSingleInstanceLock) return;
  try {
    await startBackend();
    createWindow();
    setupAutoUpdater();
  } catch (e) {
    console.error('[electron] 启动失败:', e);
    killBackend();
    app.exit(1);
  }
});

app.on('window-all-closed', () => {
  killBackend();
  app.quit();
});

app.on('before-quit', killBackend);
process.on('exit', killBackend);
