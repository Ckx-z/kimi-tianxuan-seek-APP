import path from "path"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"
import { inspectAttr } from 'kimi-plugin-inspect-react'

// https://vite.dev/config/
export default defineConfig({
  // 必须用绝对路径 '/'：本应用经 FastAPI 以 http://localhost:<port>/ 提供，
  // 多级路由（如 /toolbox/query）下相对 base './' 会把 ./assets 解析成
  // /toolbox/assets → 命中 SPA 回退返回 HTML → module script MIME 报错白屏。
  // （2026-08-24 查询打分无法打开事故根因）
  base: '/',
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
