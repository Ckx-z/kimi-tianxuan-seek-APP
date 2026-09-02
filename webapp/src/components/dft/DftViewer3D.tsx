/**
 * 3D 结合构象查看器（3Dmol.js · npm 包 3dmol，MIT，UMD 构建）。
 * 渲染复合物优化后 xyz，展示两个分子的相对摆放：stick 模型 +
 * 按 fragment_ranges（0 基、左闭右开）把 xyz 拆成两个 model 分别着色
 * （主体青色碳 / 客体品红碳），可选半透明 VDW 表面。
 *
 * 懒加载：默认折叠；展开时才动态 import('3dmol') 并初始化 viewer，
 * 避免 3Dmol（约 1MB）拖慢 DFT 页首屏。
 */
import { useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { ChevronDown, ChevronRight, Loader2 } from 'lucide-react';
import type { DftFragmentRanges } from './api';

interface Props {
  /** 复合物优化后 xyz 文本（result.complex_xyz） */
  xyz: string;
  /** 两片段原子序区间；缺失时整体单色渲染 */
  fragmentRanges?: DftFragmentRanges | null;
  /** 片段标注（dimer 模式：二聚体 / X；pair 模式：分子 A / 分子 B） */
  labelA?: string;
  labelB?: string;
  /** 折叠标题（默认复合物口径；构象详情等场景可覆盖） */
  title?: string;
}

/** 按片段区间把复合物 xyz 拆成两个独立 xyz 文本；区间非法时返回 null */
function splitXyz(xyz: string, frag: DftFragmentRanges): [string, string] | null {
  const lines = xyz.trim().split('\n');
  const n = parseInt(lines[0], 10);
  if (!Number.isFinite(n) || n <= 0 || lines.length < n + 2) return null;
  const atoms = lines.slice(2, 2 + n);
  const [a0, a1] = frag.a;
  const [b0, b1] = frag.b;
  if (a0 !== 0 || a1 > b0 || b1 > n || a1 <= a0 || b1 <= b0) return null;
  const mk = (slice: string[]) => `${slice.length}\nfragment\n${slice.join('\n')}\n`;
  return [mk(atoms.slice(a0, a1)), mk(atoms.slice(b0, b1))];
}

export default function DftViewer3D({ xyz, fragmentRanges, labelA = '主体', labelB = '客体',
  title = '3D 结合构象（两分子相对摆放）' }: Props) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showSurface, setShowSurface] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const viewerRef = useRef<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const libRef = useRef<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const modelsRef = useRef<{ a: any; b: any } | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const surfacesRef = useRef<any[]>([]);

  /** 展开时懒加载 3dmol 并初始化 viewer（仅首次） */
  useEffect(() => {
    if (!open || viewerRef.current || !xyz.trim()) return;
    let disposed = false;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const mod = await import('3dmol');
        // UMD：vite 下命名空间即导出（createViewer 等）；兼容 default 形态
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const $3Dmol: any = (mod as any).createViewer ? mod : (mod as any).default;
        if (disposed || !containerRef.current) return;
        const viewer = $3Dmol.createViewer(containerRef.current, {
          backgroundColor: 'white',
          antialias: true,
        });
        const parts = fragmentRanges ? splitXyz(xyz, fragmentRanges) : null;
        if (parts) {
          const mA = viewer.addModel(parts[0], 'xyz');
          const mB = viewer.addModel(parts[1], 'xyz');
          mA.setStyle({}, { stick: { radius: 0.15, colorscheme: 'cyanCarbon' } });
          mB.setStyle({}, { stick: { radius: 0.15, colorscheme: 'magentaCarbon' } });
          modelsRef.current = { a: mA, b: mB };
        } else {
          viewer.addModel(xyz, 'xyz');
          viewer.setStyle({}, { stick: { radius: 0.15 } });
          modelsRef.current = null;
        }
        viewer.zoomTo();
        viewer.render();
        viewerRef.current = viewer;
        libRef.current = $3Dmol;
      } catch (e) {
        if (!disposed) {
          setError(`3D 查看器初始化失败：${e instanceof Error ? e.message : '未知错误'}`);
        }
      } finally {
        if (!disposed) setLoading(false);
      }
    })();
    return () => { disposed = true; };
  }, [open, xyz, fragmentRanges]);

  /** 折叠/卸载时销毁 viewer，释放 WebGL 上下文 */
  useEffect(() => {
    if (open) return;
    if (viewerRef.current) {
      try { viewerRef.current.clear(); } catch { /* 忽略销毁异常 */ }
      viewerRef.current = null;
      libRef.current = null;
      modelsRef.current = null;
      surfacesRef.current = [];
    }
  }, [open]);

  useEffect(() => () => {
    if (viewerRef.current) {
      try { viewerRef.current.clear(); } catch { /* ignore */ }
      viewerRef.current = null;
    }
  }, []);

  /** 半透明 VDW 表面开关（按片段着色，与 stick 一致）
   *  v1.5.3 加固：显式十六进制色 + opacity（不依赖 colorscheme 对表面的
   *  支持差异），异常兜底并回滚开关；两片段分别加表面后统一 render。 */
  const toggleSurface = () => {
    const viewer = viewerRef.current;
    const $3Dmol = libRef.current;
    const next = !showSurface;
    setShowSurface(next);
    if (!viewer || !$3Dmol) return;
    if (!next) {
      for (const h of surfacesRef.current) {
        try { viewer.removeSurface(h); } catch { /* ignore */ }
      }
      surfacesRef.current = [];
      viewer.render();
      return;
    }
    try {
      if (modelsRef.current) {
        surfacesRef.current = [
          viewer.addSurface($3Dmol.SurfaceType.VDW,
            { opacity: 0.35, color: '#22d3ee' },   // cyan-400（分子 A）
            modelsRef.current.a.selectedAtoms({})),
          viewer.addSurface($3Dmol.SurfaceType.VDW,
            { opacity: 0.35, color: '#e879f9' },   // fuchsia-400（分子 B）
            modelsRef.current.b.selectedAtoms({})),
        ];
      } else {
        surfacesRef.current = [
          viewer.addSurface($3Dmol.SurfaceType.VDW,
            { opacity: 0.35, color: '#94a3b8' }, {}),
        ];
      }
    } catch {
      setShowSurface(false);
      surfacesRef.current = [];
    }
    viewer.render();
  };

  return (
    <div className="rounded-lg border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-2 text-left text-sm font-medium hover:bg-accent"
      >
        <span>{title}</span>
        {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
      </button>
      {open && (
        <div className="space-y-2 border-t p-3">
          {loading && (
            <div className="flex h-80 items-center justify-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在加载 3D 查看器…
            </div>
          )}
          {error && <p className="text-xs text-red-700 dark:text-red-400">{error}</p>}
          <div
            ref={containerRef}
            className="h-80 w-full rounded border bg-white"
            style={{ position: 'relative' }}
          />
          {!loading && !error && (
            <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
              {fragmentRanges ? (
                <>
                  <span className="flex items-center gap-1">
                    <span className="inline-block h-2.5 w-2.5 rounded-full bg-cyan-500" />
                    {labelA}（青色碳）
                  </span>
                  <span className="flex items-center gap-1">
                    <span className="inline-block h-2.5 w-2.5 rounded-full bg-fuchsia-500" />
                    {labelB}（品红碳）
                  </span>
                </>
              ) : (
                <span>该结果未记录片段区间，整体单色显示</span>
              )}
              <Button variant="outline" size="sm" className="h-6 px-2 text-xs" onClick={toggleSurface}>
                {showSurface ? '隐藏表面' : '显示半透明表面'}
              </Button>
              <span>拖动旋转 · 滚轮缩放</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
