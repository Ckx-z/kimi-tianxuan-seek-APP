/**
 * Ketcher raphael require 垫片（画板白屏修复，2026-08-25）。
 *
 * 根因：ketcher 渲染链路中打了 `typeof window<"u" ? require("raphael") : void 0`
 * 的条件式 require（CJS 遗留写法）。vite 构建后浏览器/Electron 渲染进程没有
 * 全局 require → KetcherPanel chunk 求值即抛 ReferenceError → React.lazy 拒绝
 * → 无错误边界时整棵树卸载 → 白屏。
 *
 * 处理：在 ketcher-react 之前注入最小 require 垫片，仅应答 'raphael'
 * （返回真实 raphael 模块，其他 id 返回 undefined——调用方均有容错）。
 * 本模块必须在 KetcherPanel.tsx 中第一个 import（ESM 求值顺序保证）。
 */
import Raphael from 'raphael';

const w = typeof window !== 'undefined'
  ? (window as unknown as {
      require?: (id: string) => unknown;
      global?: unknown;
    })
  : undefined;

if (w && typeof w.require !== 'function') {
  w.require = (id: string) => (id === 'raphael' ? Raphael : undefined);
}

// Indigo WASM / ketcher 内部还有 Node 风格 `global` 引用（浏览器未定义 →
// 画板异步初始化失败、编辑器空白）。映射到 globalThis。
if (w && typeof w.global === 'undefined') {
  w.global = window;
}

export {};
