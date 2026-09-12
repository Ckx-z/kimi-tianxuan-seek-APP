/**
 * UI 截图自查脚本（开发用，不随应用打包）。
 *
 * 用途：给「界面是否真的好看/是否真的坏了」提供客观依据 —— 逐页离屏渲染并截图，
 * 让 agent/开发者能直接看图定位问题（本轮已借此修掉侧栏不可读、OOD [Object object]、
 * NaN%、ISO 时间戳、卡片不齐等缺陷）。
 *
 * 用法（在 webapp 目录执行，需先起后端并把 webapp/dist 同步到其后端资源目录）：
 *   npx electron ../scripts/ui_screenshot.cjs
 *
 * 环境变量：
 *   SHOOT_BASE   后端地址（默认 http://127.0.0.1:8902）
 *   SHOOT_OUT    输出目录（默认 E:\cof-build\shots）
 *   SHOOT_ONLY   只截某页（home|query|batch|records|iterate|dft|mine|assistant|settings）
 *   SHOOT_DARK=1 同时截深色版（注意：应用自身主题 hook 会覆盖手动注入的 `.dark`，
 *                深色截图可能不生效——需要深色验收时请在设置页把主题切到深色后重启应用）
 *   SHOOT_FULL=1 整页捕获（按 scrollHeight 放大离屏窗口）
 *   SHOOT_SCROLL 滚动到指定 Y 再截（长页面局部特写）
 *   SHOOT_JS     截图前执行的 async JS（交互流程，如点开会话/弹窗后截图）
 *
 * 提示：助手页要看到真实对话界面需有 LLM 配置；可用「本地哑配置」避免调用付费接口
 * （base_url 指向 127.0.0.1:9，且必须写成**无 BOM** 的 UTF-8，否则后端 json.loads 失败）。
 */
const { app, BrowserWindow } = require('electron');
const fs = require('fs');
const path = require('path');

const BASE = process.env.SHOOT_BASE || 'http://127.0.0.1:8902';
const OUT = process.env.SHOOT_OUT || 'E:\\cof-build\\shots';
const ROUTES = [
  ['home', '/'],
  ['query', '/toolbox/query'],
  ['batch', '/toolbox/batch'],
  ['records', '/records'],
  ['iterate', '/iterate'],
  ['dft', '/toolbox/dft'],
  ['mine', '/mine'],
  ['assistant', '/assistant'],
  ['settings', '/settings'],
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function shoot(win, name, route, dark) {
  await win.loadURL(BASE + route);
  await sleep(2500);
  if (dark) {
    await win.webContents.executeJavaScript(
      "document.documentElement.classList.add('dark'); true;");
    await sleep(700);
  }
  // 交互脚本（点击流程后截图）：SHOOT_JS 传入 async JS，返回可序列化值
  if (process.env.SHOOT_JS) {
    await sleep(1800);
    const out = await win.webContents.executeJavaScript(process.env.SHOOT_JS);
    console.log('js result:', JSON.stringify(out).slice(0, 300));
    await sleep(2200);
  }
  // 指定滚动位置（长页面的局部特写）
  if (process.env.SHOOT_SCROLL) {
    await win.webContents.executeJavaScript(
      `window.scrollTo(0, ${Number(process.env.SHOOT_SCROLL)}); true;`);
    await sleep(900);
  }
  // 整页模式：按文档高度放大离屏窗口后再截（长页面一次看全）
  if (process.env.SHOOT_FULL === '1') {
    const h = await win.webContents.executeJavaScript(
      'Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)');
    const capped = Math.min(Math.max(h, 900), 7000);
    win.setSize(1440, capped);
    await sleep(900);
  }
  const img = await win.webContents.capturePage();
  const file = path.join(OUT, `${name}${dark ? '-dark' : ''}.png`);
  fs.writeFileSync(file, img.toPNG());
  console.log('saved', file, img.getSize());
}

app.commandLine.appendSwitch('disable-gpu');
app.whenReady().then(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    show: false,
    webPreferences: { offscreen: true, contextIsolation: true },
  });
  const only = process.env.SHOOT_ONLY;
  for (const [name, route] of ROUTES) {
    if (only && only !== name) continue;
    try {
      await shoot(win, name, route, false);
      if (process.env.SHOOT_DARK === '1') {
        await shoot(win, name, route, true);
      }
    } catch (e) {
      console.log('FAIL', name, e.message);
    }
  }
  app.quit();
});
