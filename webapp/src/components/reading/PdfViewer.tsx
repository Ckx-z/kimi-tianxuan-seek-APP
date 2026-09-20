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
import { ChevronLeft, ChevronRight, Loader2, ZoomIn, ZoomOut } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

export function PdfViewer({
  src,
  onSelectText,
}: {
  src: string;
  /** 选中文本回调（第二参为页码，供批注自动填页码） */
  onSelectText?: (text: string, page: number) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const textRef = useRef<HTMLDivElement | null>(null);
  const docRef = useRef<pdfjs.PDFDocumentProxy | null>(null);
  const renderTaskRef = useRef<{ cancel: () => void } | null>(null);
  const [numPages, setNumPages] = useState(0);
  const [page, setPage] = useState(1);
  const [scale, setScale] = useState(1.2);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [pageInput, setPageInput] = useState('1');
  const [hint, setHint] = useState('');

  // 载入文档
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    docRef.current?.destroy();
    docRef.current = null;
    pdfjs.getDocument({ url: src }).promise
      .then((doc) => {
        if (cancelled) { void doc.destroy(); return; }
        docRef.current = doc;
        setNumPages(doc.numPages);
        setPage(1);
        setPageInput('1');
        setLoading(false);
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
    onSelectText?.(text, page);
    setHint(`已选中 ${text.length} 字（第 ${page} 页）→ 已填入右侧翻译/批注框`);
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
        <div className="relative mx-auto w-fit shadow-sm">
          <canvas ref={canvasRef} className="block bg-white" />
          {/* 文字层覆盖在 canvas 上（透明文字，可选中） */}
          <div ref={textRef}
               className="pdf-text-layer absolute left-0 top-0 select-text
                          [&_span]:absolute [&_span]:whitespace-pre [&_span]:text-transparent
                          [&_span]:origin-top-left [&_span::selection]:bg-primary/30" />
        </div>
      </div>
    </div>
  );
}
