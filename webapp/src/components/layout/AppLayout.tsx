/**
 * 应用整体布局：左侧固定导航栏（紫金主题）+ 右侧内容区
 * 所有路由页面通过 <Outlet /> 渲染在内容区
 * v1.0.0：新增「工具箱」父级分组（查询打分 / 批量排序 / DFT 计算），可展开收起
 */
import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router';
import {
  Home,
  Search,
  BarChart3,
  Atom,
  FlaskConical,
  Lightbulb,
  Bot,
  Star,
  Settings,
  Moon,
  Sun,
  Toolbox,
  ChevronDown,
  ChevronRight,
  AlertTriangle,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useTheme } from '@/hooks/use-theme';
import DftGlobalChip from '@/components/dft/DftGlobalChip';

/** preload 暴露的版本不一致事件 payload（浏览器 dev 模式下无此 API） */
interface BackendVersionMismatchPayload {
  backendVersion: string;
  appVersion: string;
}
interface VersionHandshakeApi {
  onBackendVersionMismatch(cb: (payload: BackendVersionMismatchPayload) => void): () => void;
}
function getVersionHandshake(): VersionHandshakeApi | undefined {
  return (window as unknown as { updater?: VersionHandshakeApi }).updater;
}

// 主导航项（首页与工具箱分组单独渲染；设置单独放在侧栏底部）
const NAV_ITEMS = [
  { to: '/records', label: '实验记录', icon: FlaskConical },
  { to: '/iterate', label: '方案迭代', icon: Lightbulb },
  { to: '/assistant', label: '科研助手', icon: Bot },
  { to: '/mine', label: '我的', icon: Star },
];

// 「工具箱」子菜单（原 /query /batch 迁入；旧路径由路由层重定向兼容）
const TOOLBOX_ITEMS = [
  { to: '/toolbox/query', label: '查询打分', icon: Search },
  { to: '/toolbox/batch', label: '批量排序', icon: BarChart3 },
  { to: '/toolbox/dft', label: 'DFT 计算', icon: Atom },
];

/** 导航链接通用样式（激活态紫金高亮） */
function navLinkClass({ isActive }: { isActive: boolean }) {
  return cn(
    'flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors',
    isActive
      ? 'bg-sidebar-accent text-sidebar-accent-foreground shadow-[inset_2px_0_0_0_hsl(var(--gold))]'
      : 'text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground',
  );
}

export default function AppLayout() {
  const { theme, toggleTheme } = useTheme();
  const location = useLocation();
  // 子路由激活时父分组保持高亮；旧路径（/query /batch）重定向前也兜底算入
  const toolboxActive =
    location.pathname.startsWith('/toolbox') ||
    location.pathname === '/query' ||
    location.pathname === '/batch';
  const [toolboxOpen, setToolboxOpen] = useState(true);
  // 后端/界面版本不一致（主进程握手事件）→ 页面顶部红色横幅
  const [versionMismatch, setVersionMismatch] = useState<BackendVersionMismatchPayload | null>(null);

  useEffect(() => {
    const api = getVersionHandshake();
    if (!api) return; // 浏览器 dev 模式无 preload，跳过
    return api.onBackendVersionMismatch(setVersionMismatch);
  }, []);

  return (
    <div className="flex min-h-screen bg-background">
      {/* 后端版本握手失败横幅：固定置顶，指引用户完全退出后重开 */}
      {versionMismatch && (
        <div className="fixed inset-x-0 top-0 z-50 flex items-center justify-center gap-2 bg-red-600 px-4 py-2 text-sm font-medium text-white shadow-md">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span>
            后端版本 (v{versionMismatch.backendVersion}) 与界面版本 (v{versionMismatch.appVersion})
            不一致，请完全退出软件后重新打开
          </span>
        </div>
      )}
      {/* 左侧固定导航栏 */}
      <aside className="fixed inset-y-0 left-0 z-30 flex w-60 flex-col bg-sidebar-background text-sidebar-foreground">
        {/* Logo 区 */}
        <div className="px-5 pb-4 pt-6">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg gradient-royal text-base font-bold text-white">
              C
            </div>
            <div>
              <div className="text-base font-semibold tracking-wide">COF 科研助手</div>
              <div className="text-[11px] text-sidebar-foreground/50">机器学习实验平台</div>
            </div>
          </div>
          {/* 紫金渐变装饰条 */}
          <div className="mt-4 h-0.5 rounded-full gradient-royal" />
        </div>

        {/* 主导航 */}
        <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-2">
          {/* 首页固定在分组之上 */}
          <NavLink to="/" end className={navLinkClass}>
            <Home className="h-4 w-4 shrink-0" />
            <span>首页</span>
          </NavLink>

          {/* 工具箱：可展开/收起的父级分组 */}
          <div>
            <button
              type="button"
              onClick={() => setToolboxOpen((v) => !v)}
              aria-expanded={toolboxOpen}
              className={cn(
                'flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors',
                toolboxActive
                  ? 'text-sidebar-accent-foreground'
                  : 'text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground',
              )}
            >
              <Toolbox className="h-4 w-4 shrink-0" />
              <span className="flex-1 text-left">工具箱</span>
              {toolboxOpen ? (
                <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-60" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5 shrink-0 opacity-60" />
              )}
            </button>
            {toolboxOpen && (
              <div className="mt-1 space-y-1">
                {TOOLBOX_ITEMS.map(({ to, label, icon: Icon }) => (
                  <NavLink
                    key={to}
                    to={to}
                    className={(state) => cn(navLinkClass(state), 'pl-10')}
                  >
                    <Icon className="h-4 w-4 shrink-0" />
                    <span className="flex-1">{label}</span>
                  </NavLink>
                ))}
              </div>
            )}
          </div>

          {/* 其余主导航 */}
          {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} className={navLinkClass}>
              <Icon className="h-4 w-4 shrink-0" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>

        {/* 底部：暗色切换 + 设置 */}
        <div className="space-y-1 border-t border-sidebar-border px-3 py-3">
          <button
            type="button"
            onClick={toggleTheme}
            className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium text-sidebar-foreground/70 transition-colors hover:bg-sidebar-accent/50 hover:text-sidebar-foreground"
          >
            {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            <span>{theme === 'dark' ? '浅色模式' : '暗色模式'}</span>
          </button>
          <NavLink to="/settings" className={navLinkClass}>
            <Settings className="h-4 w-4 shrink-0" />
            <span>设置</span>
          </NavLink>
        </div>
      </aside>

      {/* 右侧内容区 */}
      <main className="ml-60 flex-1">
        <div className="mx-auto w-full max-w-6xl px-8 py-8">
          <Outlet />
        </div>
      </main>

      {/* DFT 全局任务悬浮徽标（所有页面可见，跨页进度 + 完成通知入口） */}
      <DftGlobalChip />
    </div>
  );
}
