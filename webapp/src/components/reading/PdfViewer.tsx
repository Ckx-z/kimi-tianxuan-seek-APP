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
// F1d 关键修复：**必须引入 pdf.js 官方文字层样式**。
// 官方 CSS 负责 span 的 transform: scale(var(--scale-factor))、overflow 裁剪等；
// 之前用手写 Tailwind 类代替 → span 定位与容器缩放不匹配 → 文字层溢出容器、
// 盖到整个界面上（表现为"界面混乱/文字重叠"），同时划词也对不准。
import 'pdfjs-dist/web/pdf_viewer.css';
import { ChevronLeft, ChevronRight, List, Loader2, ZoomIn, ZoomOut } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

export function PdfViewer({
  src,
  onSelectText,
  annotations = [],
  onCaptureRegion,
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
  /** v1.9.8：拖框截图的回调（base64 PNG + 页码 + 归一化坐标） */
  onCaptureRegion?: (base64: string, page: number, position: {
    x: number; y: number; w: number; h: number }) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const textRef = useRef<HTMLDivElement | null>(null);
  const pageRef = useRef<HTMLDivElement | null>(null);
  /** F1b：滚动容器（用于自适应缩放时测量可用宽高） */
  const scrollRef = useRef<HTMLDivElement | null>(null);
  /** F4：用户是否手动缩放过 —— 手动之后拖动面板不再强行覆盖其缩放选择 */
  const userZoomRef = useRef(false);
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
  /** v1.9.8 R2 增强：文档内查找 */
  const [findQ, setFindQ] = useState('');
  const [findHits, setFindHits] = useState<{ page: number; snippet: string }[]>([]);
  const [finding, setFinding] = useState(false);
  const [showFind, setShowFind] = useState(false);
  /** v1.9.8 R2 增强：整页双语对照 */
  const [biOn, setBiOn] = useState(false);
  const [biBusy, setBiBusy] = useState(false);
  const [biPage, setBiPage] = useState(0);
  const [biPairs, setBiPairs] = useState<{ src: string; dst: string }[]>([]);
  /** v1.9.8 R2 增强：区域截图（拖框 → canvas 取像素 → base64 回调） */
  const [captureOn, setCaptureOn] = useState(false);
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number }
    | null>(null);

  /** 从 canvas 裁出选中区域并回调 base64（归一化坐标一并回传，便于入库与复现） */
  const finishCapture = useCallback(() => {
    const canvas = canvasRef.current;
    const box = pageRef.current?.getBoundingClientRect();
    if (!canvas || !box || !drag) return;
    const left = Math.min(drag.x0, drag.x1);
    const top = Math.min(drag.y0, drag.y1);
    const w = Math.abs(drag.x1 - drag.x0);
    const h = Math.abs(drag.y1 - drag.y0);
    setDrag(null);
    setCaptureOn(false);
    if (w < 6 || h < 6) return;                    // 误点忽略
    const sx = Math.round(left * (canvas.width / box.width));
    const sy = Math.round(top * (canvas.height / box.height));
    const sw = Math.max(1, Math.round(w * (canvas.width / box.width)));
    const sh = Math.max(1, Math.round(h * (canvas.height / box.height)));
    const off = document.createElement('canvas');
    off.width = sw;
    off.height = sh;
    const ctx = off.getContext('2d');
    if (!ctx) return;
    ctx.drawImage(canvas, sx, sy, sw, sh, 0, 0, sw, sh);
    const position = {
      x: +(left / box.width).toFixed(4), y: +(top / box.height).toFixed(4),
      w: +(w / box.width).toFixed(4), h: +(h / box.height).toFixed(4),
    };
    off.toBlob((blob) => {
      if (!blob) return;
      const reader = new FileReader();
      reader.onload = () => {
        const dataUrl = String(reader.result || '');
        const b64 = dataUrl.split(',', 2)[1] || '';
        if (b64) onCaptureRegion?.(b64, page, position);
      };
      reader.readAsDataURL(blob);
    }, 'image/png');
  }, [drag, page, onCaptureRegion]);

  /** 取当前页文字 → 切段 → 调后端段落翻译（同段走缓存，不重复计费） */
  const loadBilingual = useCallback(async (targetPage: number) => {
    const doc = docRef.current;
    if (!doc) return;
    setBiBusy(true);
    setBiPairs([]);
    try {
      const p = await doc.getPage(targetPage);
      const content = await p.getTextContent();
      const raw = (content.items as { str?: string }[]).map((it) => it.str || '').join(' ');
      // 简单切段：按句子聚合到 ~360 字符，避免一句话被切断
      const sentences = raw.split(/(?<=[.!?。！？])\s+/);
      const segments: string[] = [];
      let buf = '';
      for (const s of sentences) {
        if (!s.trim()) continue;
        buf = buf ? `${buf} ${s}` : s;
        if (buf.length >= 360) { segments.push(buf); buf = ''; }
      }
      if (buf.trim()) segments.push(buf);
      const use = segments.slice(0, 12);            // 单页最多 12 段，控制成本
      if (use.length === 0) { toast.info('本页没有可提取的文字（可能是扫描件）'); return; }
      const r = await fetch('/api/library/reading/translate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ segments: use }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || `翻译失败（${r.status}）`);
      const dst: string[] = d.translations ?? [];
      setBiPairs(use.map((s, i) => ({ src: s, dst: dst[i] || '' })));
      setBiPage(targetPage);
      if (d.cached) toast.info(`其中 ${d.cached} 段命中缓存（未重复计费）`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '翻译失败');
    } finally {
      setBiBusy(false);
    }
  }, []);

  /** 跨页查找（逐页取文字层；最多扫 80 页，避免超大 PDF 卡住界面） */
  const runFind = useCallback(async () => {
    const doc = docRef.current;
    const needle = findQ.trim().toLowerCase();
    if (!doc || !needle) return;
    setFinding(true);
    setFindHits([]);
    try {
      const hits: { page: number; snippet: string }[] = [];
      const maxPage = Math.min(doc.numPages, 80);
      for (let i = 1; i <= maxPage; i += 1) {
        const p = await doc.getPage(i);
        const content = await p.getTextContent();
        const text = (content.items as { str?: string }[])
          .map((it) => it.str || '').join(' ');
        const idx = text.toLowerCase().indexOf(needle);
        if (idx >= 0) {
          hits.push({ page: i,
                      snippet: text.slice(Math.max(0, idx - 20), idx + needle.length + 30) });
        }
        if (hits.length >= 50) break;
      }
      setFindHits(hits);
      if (hits.length > 0) go(hits[0].page);
      else toast.info(`未找到「${findQ.trim()}」（已扫描前 ${maxPage} 页）`);
    } catch {
      toast.error('查找失败');
    } finally {
      setFinding(false);
    }
  }, [findQ]);

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
    // F1c：**立刻清空文字层**（之前在被取消的渲染路径上会残留上一页的 span →
    // 表现就是"翻页后上一页的字卡在这一页"）
    if (textRef.current) textRef.current.innerHTML = '';
    try {
      const p = await doc.getPage(page);
      const viewport = p.getViewport({ scale });
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      renderTaskRef.current?.cancel();
      // F1：按**屏幕像素比**渲染（此前直接用 CSS 尺寸 → 高分屏上字迹发虚）
      const dpr = Math.max(1, Math.min(3, window.devicePixelRatio || 1));
      const cssW = Math.floor(viewport.width);
      const cssH = Math.floor(viewport.height);
      canvas.width = Math.floor(cssW * dpr);
      canvas.height = Math.floor(cssH * dpr);
      canvas.style.width = `${cssW}px`;
      canvas.style.height = `${cssH}px`;
      const task = p.render({
        canvasContext: ctx, viewport, canvas,
        transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined,
      } as unknown as Parameters<typeof p.render>[0]);
      renderTaskRef.current = task as unknown as { cancel: () => void };
      await task.promise;

      // 文字层：让用户能像 PDF 阅读器一样选中文字
      const holder = textRef.current;
      if (holder) {
        holder.innerHTML = '';
        holder.style.width = `${cssW}px`;
        holder.style.height = `${cssH}px`;
        // F1 关键：pdf.js 4.x 的文字层用 CSS 变量 --scale-factor 定位每个 span；
        // 不设它 → span 与画布文字错位 → "划不中/选不准"（此前正是这个原因）
        holder.style.setProperty('--scale-factor', String(scale));
        holder.style.setProperty('--total-scale-factor', String(scale));
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

  /** 按容器可用宽高算自适应缩放；返回 null 表示尺寸异常/取页失败 → 保持当前缩放 */
  const fitScale = useCallback(async (): Promise<number | null> => {
    const doc = docRef.current;
    const box = scrollRef.current;
    if (!doc || !box) return null;
    try {
      const p = await doc.getPage(1);
      const base = p.getViewport({ scale: 1 });
      const availW = Math.max(200, box.clientWidth - 28);
      const availH = Math.max(200, box.clientHeight - 28);
      const fit = Math.min(availW / base.width, availH / base.height);
      if (fit > 0.3 && fit < 4) return +fit.toFixed(3);
    } catch { /* 自适应失败则保持当前缩放 */ }
    return null;
  }, []);

  /** F1b：文档载入后**自适应容器**（默认铺满可视区，避免页面显小、四周留大片空白） */
  useEffect(() => {
    if (numPages === 0) return;
    userZoomRef.current = false;      // 换文档 → 重新允许自适应接管
    let cancelled = false;
    (async () => {
      const s = await fitScale();
      if (!cancelled && s) setScale(s);
    })();
    return () => { cancelled = true; };
    // numPages 变化（换文档）时重算；scale 变化不重算，避免用户手动缩放被覆盖
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [numPages, fitScale]);

  /**
   * F4：容器尺寸变化（拖动左右侧栏、窗口缩放、折叠面板）后**立即重算自适应**。
   * 修的是「拖宽侧栏后 PDF 还是原来那个尺寸、右侧留大片空白」。
   * 已手动缩放过的（点了 +/-）不覆盖，避免与用户意图打架。
   */
  useEffect(() => {
    const box = scrollRef.current;
    if (!box || numPages === 0) return;
    let timer: number | undefined;
    let lastSize = `${box.clientWidth}x${box.clientHeight}`;
    const ro = new ResizeObserver(() => {
      const now = `${box.clientWidth}x${box.clientHeight}`;
      if (now === lastSize) return;      // 高度随内容变化会自触发 → 尺寸没变就跳过
      lastSize = now;
      if (userZoomRef.current) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(async () => {
        const s = await fitScale();
        // 变化 <1% 就不重渲染：避免「改尺寸→重算→内容高度变→再触发」来回抖
        if (s) setScale((cur) => (Math.abs(cur - s) > 0.01 ? s : cur));
      }, 120);                            // 拖动过程中防抖，松手后落定
    });
    ro.observe(box);
    return () => { ro.disconnect(); window.clearTimeout(timer); };
  }, [numPages, fitScale]);

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
    <div className="flex h-full min-h-[460px] flex-col">
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
                title="缩小"
                onClick={() => { userZoomRef.current = true;
                                 setScale((s) => Math.max(0.5, +(s - 0.2).toFixed(2))); }}>
          <ZoomOut className="h-3.5 w-3.5" />
        </Button>
        {/* F4：点百分比 → 回到「自适应容器」（清除手动缩放标记，拖侧栏又会自动跟随） */}
        <button type="button" title="点击恢复自适应容器"
                className="w-11 rounded px-1 text-center text-[11px] text-muted-foreground
                           hover:bg-accent hover:text-foreground"
                onClick={async () => {
                  userZoomRef.current = false;
                  const s = await fitScale();
                  if (s) setScale(s);
                  setHint('已恢复自适应容器大小');
                }}>
          {Math.round(scale * 100)}%
        </button>
        <Button size="sm" variant="outline" className="h-7 w-7 p-0"
                title="放大"
                onClick={() => { userZoomRef.current = true;
                                 setScale((s) => Math.min(3, +(s + 0.2).toFixed(2))); }}>
          <ZoomIn className="h-3.5 w-3.5" />
        </Button>
        <span className="ml-auto text-[11px] text-muted-foreground">
          {hint || '选中文字即可翻译 / 记批注'}
        </span>
        {/* v1.9.8 R2 增强：文档内查找 */}
        <div className="relative">
          <Button size="sm" variant="outline" className="h-7 px-2 text-[11px]"
                  onClick={() => setShowFind((v) => !v)}>
            查找{findHits.length > 0 ? `（${findHits.length}）` : ''}
          </Button>
          {showFind && (
            <div className="absolute right-0 z-10 mt-1 w-80 rounded-md border border-border
                            bg-popover p-2 shadow-md">
              <div className="flex items-center gap-1">
                <Input value={findQ} onChange={(e) => setFindQ(e.target.value)}
                       onKeyDown={(e) => { if (e.key === 'Enter') void runFind(); }}
                       placeholder="在本文中查找…" className="h-7 text-xs" />
                <Button size="sm" className="h-7 px-2 text-[11px]" disabled={finding}
                        onClick={() => void runFind()}>
                  {finding ? <Loader2 className="h-3 w-3 animate-spin" /> : '搜索'}
                </Button>
              </div>
              {findHits.length > 0 && (
                <ul className="mt-1.5 max-h-56 overflow-auto">
                  {findHits.map((h) => (
                    <li key={h.page}>
                      <button type="button"
                              onClick={() => { go(h.page); setShowFind(false); }}
                              className="w-full rounded px-1.5 py-1 text-left text-[11px]
                                         hover:bg-accent">
                        <span className="font-medium">p.{h.page}</span>
                        <span className="ml-1 text-muted-foreground">…{h.snippet}…</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
        {/* v1.9.8 R2 增强：区域截图（拖框截取当前页） */}
        <Button size="sm" variant={captureOn ? 'default' : 'outline'}
                className="h-7 px-2 text-[11px]"
                title="开启后在页面上按住拖框，即可把该区域存为图片批注"
                onClick={() => { setCaptureOn((v) => !v); setDrag(null); }}>
          截图{captureOn ? '中…' : ''}
        </Button>
        {/* v1.9.8 R2 增强：整页双语对照（段落级翻译，后端按段缓存） */}
        <Button size="sm" variant={biOn ? 'default' : 'outline'} className="h-7 px-2 text-[11px]"
                disabled={biBusy}
                title="把当前页按段落翻译成中文，左侧原文右侧译文"
                onClick={() => { const next = !biOn; setBiOn(next); if (next) void loadBilingual(page); }}>
          双语{biBusy ? '…' : biOn ? '开' : '对照'}
        </Button>
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

      <div ref={scrollRef} className="relative min-h-0 flex-1 overflow-auto bg-muted/10 p-3"
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
        <div ref={pageRef} className="relative mx-auto w-fit shadow-sm"
             style={captureOn ? { cursor: 'crosshair' } : undefined}
             onMouseDown={(e) => {
               if (!captureOn) return;
               const box = pageRef.current?.getBoundingClientRect();
               if (!box) return;
               e.preventDefault();
               const x = e.clientX - box.left;
               const y = e.clientY - box.top;
               setDrag({ x0: x, y0: y, x1: x, y1: y });
             }}
             onMouseMove={(e) => {
               if (!drag || !captureOn) return;
               const box = pageRef.current?.getBoundingClientRect();
               if (!box) return;
               setDrag({ ...drag, x1: e.clientX - box.left, y1: e.clientY - box.top });
             }}
             onMouseUp={() => { if (captureOn && drag) finishCapture(); }}>
          <canvas ref={canvasRef} className="block bg-white" />
          {/* 拖框提示矩形 */}
          {captureOn && drag && (
            <div className="pointer-events-none absolute border-2 border-primary/70
                            bg-primary/10"
                 style={{ left: Math.min(drag.x0, drag.x1), top: Math.min(drag.y0, drag.y1),
                          width: Math.abs(drag.x1 - drag.x0),
                          height: Math.abs(drag.y1 - drag.y0) }} />
          )}
          {/* 文字层：使用 pdf.js 官方 `.textLayer` 样式（F1d），容器自身裁剪溢出 */}
          <div ref={textRef}
               className="textLayer"
               style={{ position: 'absolute', left: 0, top: 0, overflow: 'hidden' }} />
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
        {/* v1.9.8 R2 增强：双语对照栏（左原文右译文，段落级） */}
        {biOn && (
          <div className="absolute right-2 top-2 z-10 max-h-[560px] w-[420px] overflow-auto
                          rounded-md border border-border bg-background/95 p-2 shadow-lg">
            <div className="mb-1 flex items-center justify-between">
              <span className="text-[11px] font-medium">
                双语对照 · 第 {biPage || page} 页（{biPairs.length} 段）
              </span>
              <button type="button" className="text-[11px] text-muted-foreground hover:text-foreground"
                      onClick={() => setBiOn(false)}>关闭</button>
            </div>
            {biBusy && (
              <p className="py-4 text-center text-[11px] text-muted-foreground">
                <Loader2 className="mx-auto mb-1 h-4 w-4 animate-spin" />正在翻译本页…
              </p>
            )}
            {!biBusy && biPairs.length === 0 && (
              <p className="py-3 text-center text-[11px] text-muted-foreground">
                本页没有可取的文字（扫描件需先做 OCR）
              </p>
            )}
            <div className="space-y-2">
              {biPairs.map((pair, i) => (
                <div key={i} className="grid grid-cols-2 gap-1.5 border-b border-border/50 pb-1.5">
                  <p className="text-[11px] leading-relaxed text-muted-foreground">{pair.src}</p>
                  <p className="text-[11px] leading-relaxed">{pair.dst || '（未译）'}</p>
                </div>
              ))}
            </div>
            {biPairs.length > 0 && (
              <button type="button"
                      className="mt-1 text-[10px] text-primary hover:underline"
                      onClick={() => void loadBilingual(page)}>
                重新翻译本页（命中缓存则不计费）
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
