import path from "path"
import { execSync } from "child_process"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"
import { inspectAttr } from 'kimi-plugin-inspect-react'

// 前端构建哈希（2026-08-26 版本三显）：优先取 git 短 hash，
// 非 git 环境（如导出源码构建）退化为时间戳随机串，保证每次构建唯一。
// 经 define 注入为 __BUILD_HASH__，设置页"关于"区展示，截图即可确认真身。
const BUILD_HASH = (() => {
  try {
    return execSync('git rev-parse --short HEAD', { stdio: ['ignore', 'pipe', 'ignore'] })
      .toString()
      .trim()
  } catch {
    return 't' + Date.now().toString(36)
  }
})();

// https://vite.dev/config/
export default defineConfig({
  // 必须用绝对路径 '/'：本应用经 FastAPI 以 http://localhost:<port>/ 提供，
  // 多级路由（如 /toolbox/query）下相对 base './' 会把 ./assets 解析成
  // /toolbox/assets → 命中 SPA 回退返回 HTML → module script MIME 报错白屏。
  // （2026-08-24 查询打分无法打开事故根因）
  base: '/',
  define: {
    __BUILD_HASH__: JSON.stringify(BUILD_HASH),
  },
  plugins: [inspectAttr(), react()],
  server: {
    port: 3000,
    proxy: {
      // 开发环境将 /api 代理到本地 FastAPI 后端
      '/api': {
        target: process.env.VITE_API_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
