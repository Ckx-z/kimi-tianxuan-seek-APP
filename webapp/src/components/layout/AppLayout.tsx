/**
 * 应用整体布局：左侧固定导航栏（紫金主题）+ 右侧内容区
 * 所有路由页面通过 <Outlet /> 渲染在内容区
 */
import { NavLink, Outlet } from 'react-router';
import {
  Home,
  Search,
  BarChart3,
  FlaskConical,
  Lightbulb,
  Star,
  Settings,
  Moon,
  Sun,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { useTheme } from '@/hooks/use-theme';

// 主导航项（设置单独放在侧栏底部）
const NAV_ITEMS = [
  { to: '/', label: '首页', icon: Home, end: true },
  { to: '/query', label: '查询打分', icon: Search },
  { to: '/batch', label: '批量排序', icon: BarChart3 },
  { to: '/records', label: '实验记录', icon: FlaskConical },
  { to: '/iterate', label: '方案迭代', icon: Lightbulb },
  { to: '/mine', label: '我的', icon: Star },
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

  return (
    <div className="flex min-h-screen bg-background">
      {/* 左侧固定导航栏 */}
      <aside className="fixed inset-y-0 left-0 z-30 flex w-60 flex-col bg-sidebar-background text-sidebar-foreground">
        {/* Logo 区 */}
        <div className="px-5 pb-4 pt-6">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg gradient-royal text-base font-bold text-white">
              C
            </div>
            <div>
              <div className="text-base font-semibold tracking-wide">COF 成膜推荐</div>
              <div className="text-[11px] text-sidebar-foreground/50">机器学习实验平台</div>
            </div>
          </div>
          {/* 紫金渐变装饰条 */}
          <div className="mt-4 h-0.5 rounded-full gradient-royal" />
        </div>

        {/* 主导航 */}
        <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-2">
          {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end} className={navLinkClass}>
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
    </div>
  );
}
