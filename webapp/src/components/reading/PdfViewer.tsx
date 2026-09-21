/**
 * pdf.js 阅读器（v1.9.8 R2，替代此前的 iframe）
 *
 * 能力：翻页 / 跳页 / 缩放 / **文字层选中**（把选中文本回调给外层 → 填入翻译与批注框）。
 * 刻意保持精简：批注高亮层与区域截图在下一轮叠加（当前选中即回填，已可用）。
 *
 * worker 走 Vite 的 `?url` 导入，避免打包后找不到 pdf.worker。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import * as pdfjs from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.mjs?url';
import { ChevronLeft, ChevronRight, List, Loader2, ZoomIn, ZoomOut } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

export function PdfViewer({
  src,
  onSelectText,
  annotations = [],
}: {
  src: string;
  /** 选中文本回调（含页码与归一化坐标，供批注自动填页码并画出高亮） */
  onSelectText?: (text: string, page: number, position: {
    x: number; y: number; w: number; h: number;
    rects?: { x: number; y: number; w: number; h: number }[];
  }) => void;
  /** 该文献的批注（按页画出高亮；position 为 0~1 归一化坐标） */
  annotations?: {
    annotation_id: string; page: number | null; color: string; text: string;
    comment: string; position?: {
      x?: number; y?: number; w?: number; h?: number;
      rects?: { x: number; y: number; w: number; h: number }[];
    };
  }[];
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const textRef = useRef<HTMLDivElement | null>(null);
  const pageRef = useRef<HTMLDivElement | null>(null);
  const docRef = useRef<pdfjs.PDFDocumentProxy | null>(null);
  const renderTaskRef = useRef<{ cancel: () => void } | null>(null);
  const [numPages, setNumPages] = useState(0);
  const [page, setPage] = useState(1);
  const [scale, setScale] = useState(1.2);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [pageInput, setPageInput] = useState('1');
  const [hint, setHint] = useState('');
  /** v1.9.8 R2 增强：目录大纲 */
  const [outline, setOutline] = useState<{ title: string; page: number }[]>([]);
  const [showOutline, setShowOutline] = useState(false);

  // 载入文档
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    docRef.current?.destroy();
    docRef.current = null;
    pdfjs.getDocument({ url: src }).promise
      .then(async (doc) => {
        if (cancelled) { void doc.destroy(); return; }
        docRef.current = doc;
        setNumPages(doc.numPages);
        setPage(1);
        setPageInput('1');
        setLoading(false);
        // v1.9.8 R2 增强：目录大纲（有则显示「目录」，点击跳页）
        try {
          const outline = await doc.getOutline();
          const items: { title: string; page: number }[] = [];
          for (const it of (outline ?? []).slice(0, 60)) {
            let pageNo: number | null = null;
            try {
              const dest = typeof it.dest === 'string'
                ? await doc.getDestination(it.dest) : it.dest;
              if (Array.isArray(dest) && dest[0]) {
                pageNo = (await doc.getPageIndex(dest[0])) + 1;
              }
            } catch { /* 该条目无有效目标，跳过页码 */ }
            if (it.title) items.push({ title: String(it.title).slice(0, 80),
                                       page: pageNo ?? 1 });
          }
          if (!cancelled) setOutline(items);
        } catch { if (!cancelled) setOutline([]); }
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : 'PDF 载入失败');
        setLoading(false);
      });
    return () => { cancelled = true; };
  }, [src]);

  // 渲染当前页（canvas + 文字层）
  const render = useCallback(async () => {
    const doc = docRef.current;
    const canvas = canvasRef.current;
    if (!doc || !canvas) return;
    try {
      const p = await doc.getPage(page);
      const viewport = p.getViewport({ scale });
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      renderTaskRef.current?.cancel();
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      const task = p.render({ canvasContext: ctx, viewport,
                              canvas } as unknown as Parameters<typeof p.render>[0]);
      renderTaskRef.current = task as unknown as { cancel: () => void };
      await task.promise;

      // 文字层：让用户能像 PDF 阅读器一样选中文字
      const holder = textRef.current;
      if (holder) {
        holder.innerHTML = '';
        holder.style.width = `${Math.floor(viewport.width)}px`;
        holder.style.height = `${Math.floor(viewport.height)}px`;
        const layer = new pdfjs.TextLayer({
          textContentSource: p.streamTextContent(),
          container: holder,
          viewport,
        });
        await layer.render();
      }
    } catch (e: unknown) {
      // 渲染任务被取消属正常（快速翻页），不打扰用户
      const msg = e instanceof Error ? e.message : String(e);
      if (!/cancel/i.test(msg)) setHint(`渲染失败：${msg}`);
    }
  }, [page, scale]);

  useEffect(() => { void render(); }, [render, numPages]);

  const go = (next: number) => {
    const clamped = Math.min(Math.max(1, next), Math.max(1, numPages));
    setPage(clamped);
    setPageInput(String(clamped));
  };

  const capture = () => {
    const sel = window.getSelection();
    const text = (sel?.toString() || '').trim();
    if (!text) return;
    // 计算选中区域的**归一化坐标**（相对页面容器），供批注高亮复现
    let position = { x: 0, y: 0, w: 0, h: 0,
                     rects: [] as { x: number; y: number; w: number; h: number }[] };
    const box = pageRef.current?.getBoundingClientRect();
    const range = sel && sel.rangeCount > 0 ? sel.getRangeAt(0) : null;
    if (box && range && box.width > 0 && box.height > 0) {
      const rects = Array.from(range.getClientRects())
        .filter((r) => r.width > 1 && r.height > 1)
        .slice(0, 8)
        .map((r) => ({
          x: +((r.left - box.left) / box.width).toFixed(4),
          y: +((r.top - box.top) / box.height).toFixed(4),
          w: +(r.width / box.width).toFixed(4),
          h: +(r.height / box.height).toFixed(4),
        }));
      const first = rects[0];
      if (first) {
        position = { ...first, rects };
      }
    }
    onSelectText?.(text, page, position);
    setHint(`已选中 ${text.length} 字（第 ${page} 页）→ 已填入右侧翻译/批注框`);
  };

  /** 当前页的批注高亮（颜色 → 半透明色块） */
  const pageAnnots = annotations.filter((a) => a.page === page);
  const COLOR_CLASS: Record<string, string> = {
    yellow: 'bg-yellow-300/40 border-yellow-500/60',
    green: 'bg-green-300/40 border-green-600/60',
    blue: 'bg-blue-300/40 border-blue-600/60',
    red: 'bg-red-300/40 border-red-600/60',
  };

  return (
    <div className="flex h-[600px] flex-col">
      <div className="flex flex-wrap items-center gap-1 border-b border-border bg-muted/20 px-2 py-1.5">
        <Button size="sm" variant="outline" className="h-7 w-7 p-0"
                disabled={page <= 1} onClick={() => go(page - 1)}>
          <ChevronLeft className="h-3.5 w-3.5" />
        </Button>
        <Input value={pageInput} onChange={(e) => setPageInput(e.target.value)}
               onKeyDown={(e) => { if (e.key === 'Enter') go(Number(pageInput) || 1); }}
               className="h-7 w-14 text-center text-xs" />
        <span className="text-[11px] text-muted-foreground">/ {numPages || '-'}</span>
        <Button size="sm" variant="outline" className="h-7 w-7 p-0"
                disabled={page >= numPages} onClick={() => go(page + 1)}>
          <ChevronRight className="h-3.5 w-3.5" />
        </Button>
        <span className="mx-1 h-4 w-px bg-border" />
        <Button size="sm" variant="outline" className="h-7 w-7 p-0"
                onClick={() => setScale((s) => Math.max(0.5, +(s - 0.2).toFixed(2)))}>
          <ZoomOut className="h-3.5 w-3.5" />
        </Button>
        <span className="w-10 text-center text-[11px] text-muted-foreground">
          {Math.round(scale * 100)}%
        </span>
        <Button size="sm" variant="outline" className="h-7 w-7 p-0"
                onClick={() => setScale((s) => Math.min(3, +(s + 0.2).toFixed(2)))}>
          <ZoomIn className="h-3.5 w-3.5" />
        </Button>
        <span className="ml-auto text-[11px] text-muted-foreground">
          {hint || '选中文字即可翻译 / 记批注'}
        </span>
        {/* v1.9.8 R2 增强：目录大纲 */}
        {outline.length > 0 && (
          <div className="relative">
            <Button size="sm" variant="outline" className="h-7 px-2 text-[11px]"
                    onClick={() => setShowOutline((v) => !v)}>
              <List className="mr-1 h-3 w-3" />目录（{outline.length}）
            </Button>
            {showOutline && (
              <div className="absolute right-0 z-10 mt-1 max-h-72 w-72 overflow-auto
                              rounded-md border border-border bg-popover p-1 shadow-md">
                {outline.map((o, i) => (
                  <button key={`${o.title}-${i}`} type="button"
                          onClick={() => { go(o.page); setShowOutline(false); }}
                          className="flex w-full items-baseline justify-between gap-2 rounded
                                     px-2 py-1 text-left text-[11px] hover:bg-accent">
                    <span className="min-w-0 flex-1 truncate" title={o.title}>{o.title}</span>
                    <span className="shrink-0 text-muted-foreground">p.{o.page}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="relative min-h-0 flex-1 overflow-auto bg-muted/10 p-3"
           onMouseUp={capture}>
        {loading && (
          <p className="absolute inset-0 flex items-center justify-center text-xs text-muted-foreground">
            <Loader2 className="mr-1 h-4 w-4 animate-spin" />载入 PDF…
          </p>
        )}
        {error && (
          <p className="absolute inset-0 flex items-center justify-center p-6 text-center text-xs text-destructive">
            {error}
          </p>
        )}
        <div ref={pageRef} className="relative mx-auto w-fit shadow-sm">
          <canvas ref={canvasRef} className="block bg-white" />
          {/* 文字层覆盖在 canvas 上（透明文字，可选中） */}
          <div ref={textRef}
               className="pdf-text-layer absolute left-0 top-0 select-text
                          [&_span]:absolute [&_span]:whitespace-pre [&_span]:text-transparent
                          [&_span]:origin-top-left [&_span::selection]:bg-primary/30" />
          {/* 批注高亮层（v1.9.8 R2-p）：按归一化坐标画回页面 */}
          <div className="pointer-events-none absolute left-0 top-0 h-full w-full">
            {pageAnnots.map((a) => {
              const pos = a.position || {};
              const parts = pos.rects?.length
                ? pos.rects
                : (typeof pos.x === 'number' && typeof pos.w === 'number'
                    ? [{ x: pos.x, y: pos.y ?? 0, w: pos.w, h: pos.h ?? 0.02 }]
                    : []);
              const cls = COLOR_CLASS[a.color] || COLOR_CLASS.yellow;
              return parts.map((r, i) => (
                <div key={`${a.annotation_id}-${i}`}
                     title={a.comment || a.text || '批注'}
                     className={`absolute rounded-[2px] border ${cls}`}
                     style={{ left: `${r.x * 100}%`, top: `${r.y * 100}%`,
                              width: `${r.w * 100}%`, height: `${r.h * 100}%` }} />
              ));
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
