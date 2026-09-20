/**
 * 文献阅读（v1.9.8 R2，对标 Zotero 的阅读流程）
 *
 * 左栏 阅读清单：待读 / 在读 / 已读 / 已入库 筛选（数据来自 /api/library/reading/queue）
 * 中栏 PDF 阅读器：该文献的主文附件（/api/literature/attachments/{file_id}/file）
 * 右栏 本文状态：
 *   · 解析状态与「入库计划」——未入库→全量解析；已入库→只补缺失项（/reading/plan/{id}）
 *   · 阅读状态切换（unread / reading / read / ingested）
 *   · 批注列表与「导出为笔记」（/annotations、/reading/export-note）
 *
 * 下一轮接：pdf.js 批注层（高亮/便签/区域截图）、DeepSeek 双语对照翻译、划词提问。
 */
import { useCallback, useEffect, useState } from 'react';
import { BookOpen, CheckCircle2, FileText, Languages, Loader2, NotebookPen,
         Trash2, Upload } from 'lucide-react';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/badge';
import { PdfViewer } from '@/components/reading/PdfViewer';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';

const STATES: { key: string; label: string }[] = [
  { key: '', label: '全部' },
  { key: 'unread', label: '待读' },
  { key: 'reading', label: '在读' },
  { key: 'read', label: '已读' },
  { key: 'ingested', label: '已入库' },
];
const STATE_LABEL: Record<string, string> = {
  unread: '待读', reading: '在读', read: '已读', ingested: '已入库',
};
const PART_LABEL: Record<string, string> = {
  abstract: '摘要', entries: '结构化条目', figures: '文献图片',
  meta: '元数据',
};

interface QueueRow {
  paper_id: string; title: string; journal: string; year: number | null; doi: string;
  parse_status?: 'parsed' | 'partial' | 'none';
  missing_parts?: string[];
  reading_state: string;
  annotations: number;
}
interface Plan {
  paper_id: string; reading_state: string; parse_status?: string;
  missing_parts: string[]; action: string; parts: string[]; full_parse: boolean;
  counts: { entries: number; figures: number; has_abstract: boolean };
}
interface Attachment { file_id: string; filename: string; role: string }
interface Annotation {
  annotation_id: string; type: string; page: number | null; color: string;
  text: string; comment: string;
}

export default Reading;

function Reading() {
  const [state, setState] = useState('');
  const [queue, setQueue] = useState<QueueRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [paperId, setPaperId] = useState('');
  const [plan, setPlan] = useState<Plan | null>(null);
  const [atts, setAtts] = useState<Attachment[]>([]);
  const [annos, setAnnos] = useState<Annotation[]>([]);
  /** v1.9.8 R2-d：选中/粘贴的原文片段（下一轮由 pdf.js 真实划选直接填入） */
  const [selText, setSelText] = useState('');
  const [translation, setTranslation] = useState('');
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState('');
  const [busy, setBusy] = useState(false);
  /** R2-g：手动批注（页码 + 原文片段 + 评论 + 颜色） */
  const [annoPage, setAnnoPage] = useState('');
  const [annoComment, setAnnoComment] = useState('');
  const [annoColor, setAnnoColor] = useState('yellow');
  /** R2-p：划选区域的归一化坐标（随批注一起入库 → 可在 PDF 上画回高亮） */
  const [selPos, setSelPos] = useState<{ x: number; y: number; w: number; h: number;
    rects?: { x: number; y: number; w: number; h: number }[] } | null>(null);
  /** R2-h：入库/补缺执行中 */
  const [ingesting, setIngesting] = useState(false);
  /** R2-k：阅读收件箱（外部上传的 PDF，先读后入库） */
  const [inbox, setInbox] = useState<{ inbox_id: string; filename: string;
    state: string; size: number }[]>([]);
  const [inboxId, setInboxId] = useState('');
  const [uploading, setUploading] = useState(false);

  const loadInbox = useCallback(async () => {
    try {
      const r = await fetch('/api/library/reading/inbox?state=pending');
      const d = r.ok ? await r.json() : { inbox: [] };
      setInbox(d.inbox ?? []);
    } catch {
      setInbox([]);
    }
  }, []);

  useEffect(() => { void loadInbox(); }, [loadInbox]);

  const uploadPdf = async (file: File | undefined) => {
    if (!file) return;
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const r = await fetch('/api/library/reading/upload', { method: 'POST', body: fd });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || `上传失败（${r.status}）`);
      toast.success(d.duplicate ? '该文件已在阅读清单中' : '已加入阅读清单（尚未入库）');
      await loadInbox();
      setPaperId('');
      setInboxId(d.inbox_id);
      setPlan(null);
      setAtts([]);
      setAnnos([]);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '上传失败');
    } finally {
      setUploading(false);
    }
  };

  /** R2-n：读完一键入库 —— 建条目+挂附件（后端聚合）→ 确认后解析 → 置已入库 */
  const ingestInboxItem = async () => {
    if (!inboxId) return;
    const ok = window.confirm(
      '把这份 PDF 正式入库？\n\n本步会：① 抽取元数据并创建文献条目（编号 1782+）'
      + '② 把 PDF 作为主文附件挂到该条目。\n\n'
      + '（大模型解析不在本步执行 —— 入库后请在右栏点「提取解析并入库」，'
      + '那一步耗时数分钟并产生 token 费用。）\n\n继续？');
    if (!ok) return;
    setIngesting(true);
    try {
      const r = await fetch(`/api/library/reading/inbox/${inboxId}/ingest`,
                            { method: 'POST' });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || `入库失败（${r.status}）`);
      const pid = d.paper_id as string;
      toast.success(d.reused_existing
        ? `该 DOI 已在库中，已指向既有文献 #${pid}`
        : `已入库：文献 #${pid}（已挂主文附件）`);
      setInboxId('');
      setPaperId(pid);
      // 解析（耗 token 的一步）不在这里自动跑：入库后该文献已在阅读器打开，
      // 右栏「提取解析并入库」按钮会显示出来，由用户确认后触发（避免双重扣费）。
      toast.info('下一步：在右栏点「提取解析并入库」完成条目/图片抽取');
      await loadInbox();
      await open(pid);
      await loadQueue();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '入库失败');
    } finally {
      setIngesting(false);
    }
  };

  const discardInbox = async (id: string) => {
    try {
      await fetch(`/api/library/reading/inbox/${id}`, { method: 'DELETE' });
      if (inboxId === id) { setInboxId(''); }
      await loadInbox();
    } catch { /* 已提示 */ }
  };

  /**
   * 真触发解析入库：
   * 把该文献**已存的附件**取回，再按现有 `/api/literature/parse` 流程提交
   * （复用既有解析管线：LLM 分维度抽取 + 试校验 + 自动回填缺失元数据）。
   * 已入库的文献走同一入口 —— 解析管线按「只补空字段」语义处理，不覆盖已有内容。
   */
  const runIngest = async () => {
    if (!paperId || !main) {
      toast.error('该文献没有可解析的 PDF 附件');
      return;
    }
    const what = plan?.full_parse
      ? '全量解析（元数据 + 结构化条目 + 文献图片）'
      : `只补缺失项：${(plan?.parts ?? []).map((p) => PART_LABEL[p] ?? p).join('、')}`;
    const ok = window.confirm(
      `将执行：${what}\n\n会调用大模型解析该 PDF，可能耗时数分钟并产生 token 费用。继续？`);
    if (!ok) return;
    setIngesting(true);
    try {
      const blob = await (await fetch(
        `/api/literature/attachments/${main.file_id}/file`)).blob();
      const fd = new FormData();
      fd.append('files', new File([blob], main.filename || 'main.pdf',
                                  { type: 'application/pdf' }));
      const r = await fetch(
        `/api/literature/parse?paper_id=${encodeURIComponent(paperId)}&auto_backfill=true`,
        { method: 'POST', body: fd });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || `解析失败（${r.status}）`);
      await fetch(`/api/library/reading/state?paper_id=${encodeURIComponent(paperId)}`
                  + '&state=ingested', { method: 'POST' });
      const p = await fetch(`/api/library/reading/plan/${encodeURIComponent(paperId)}`)
        .then((x) => x.json());
      setPlan(p);
      await loadQueue();
      toast.success(`解析完成并入库（条目/图片已更新，阅读状态→已入库）`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '解析失败');
    } finally {
      setIngesting(false);
    }
  };

  const addAnnotation = async () => {
    if (!paperId || (!selText.trim() && !annoComment.trim())) return;
    try {
      const r = await fetch('/api/library/annotations', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          paper_id: paperId, type: 'highlight',
          page: annoPage.trim() ? Number(annoPage) : null,
          color: annoColor, text: selText.trim(), comment: annoComment.trim(),
          position: selPos ?? {},
        }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(d.detail || String(r.status));
      }
      setAnnoComment('');
      const n = await fetch(`/api/library/annotations?paper_id=${encodeURIComponent(paperId)}`)
        .then((x) => (x.ok ? x.json() : { annotations: [] }));
      setAnnos(n.annotations ?? []);
      toast.success('批注已保存');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '保存批注失败');
    }
  };

  const removeAnnotation = async (id: string) => {
    try {
      await fetch(`/api/library/annotations/${id}`, { method: 'DELETE' });
      setAnnos((prev) => prev.filter((x) => x.annotation_id !== id));
    } catch { /* 已提示 */ }
  };

  const doTranslate = async () => {
    const text = selText.trim();
    if (!text) return;
    setBusy(true);
    setTranslation('');
    try {
      const r = await fetch('/api/library/reading/translate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ segments: text.split(/\n{2,}/).filter(Boolean) }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || '翻译失败');
      const list: string[] = d.translations ?? [];
      setTranslation(list.filter(Boolean).join('\n\n'));
      if (d.cached) toast.info(`其中 ${d.cached} 段命中缓存（未重复计费）`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '翻译失败');
    } finally {
      setBusy(false);
    }
  };

  const doAsk = async () => {
    if (!paperId || !question.trim()) return;
    setBusy(true);
    setAnswer('');
    try {
      const r = await fetch('/api/library/reading/ask', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ paper_id: paperId, question: question.trim(),
                               selection: selText.trim() }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || '提问失败');
      setAnswer(d.answer || '');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '提问失败');
    } finally {
      setBusy(false);
    }
  };

  const loadQueue = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`/api/library/reading/queue${state ? `?state=${state}` : ''}`);
      const d = r.ok ? await r.json() : { queue: [] };
      setQueue(d.queue ?? []);
    } catch {
      setQueue([]);
    } finally {
      setLoading(false);
    }
  }, [state]);

  useEffect(() => { void loadQueue(); }, [loadQueue]);

  const open = useCallback(async (pid: string) => {
    setPaperId(pid);
    setPlan(null);
    setAtts([]);
    setAnnos([]);
    try {
      const [p, a, n] = await Promise.all([
        fetch(`/api/library/reading/plan/${encodeURIComponent(pid)}`).then((r) => r.json()),
        fetch(`/api/literature/${encodeURIComponent(pid)}/attachments`)
          .then((r) => (r.ok ? r.json() : { attachments: [] })).catch(() => ({ attachments: [] })),
        fetch(`/api/library/annotations?paper_id=${encodeURIComponent(pid)}`)
          .then((r) => (r.ok ? r.json() : { annotations: [] })).catch(() => ({ annotations: [] })),
      ]);
      setPlan(p);
      setAtts((a.attachments ?? []).filter((x: Attachment) => x.file_id));
      setAnnos(n.annotations ?? []);
    } catch {
      toast.error('加载阅读信息失败');
    }
  }, []);

  const setReadingState = async (next: string) => {
    if (!paperId) return;
    try {
      const r = await fetch(
        `/api/library/reading/state?paper_id=${encodeURIComponent(paperId)}&state=${next}`,
        { method: 'POST' });
      if (!r.ok) throw new Error(String(r.status));
      setPlan((p) => (p ? { ...p, reading_state: next } : p));
      await loadQueue();
    } catch {
      toast.error('设置阅读状态失败');
    }
  };

  const exportNote = async () => {
    if (!paperId) return;
    try {
      const r = await fetch(
        `/api/library/reading/export-note?paper_id=${encodeURIComponent(paperId)}`,
        { method: 'POST' });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(d.detail || '导出失败');
      }
      toast.success('批注已导出为笔记（见「科研知识库 → 笔记」）');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '导出失败');
    }
  };

  const main = atts.find((a) => a.role === 'main') ?? atts[0];

  return (
    <div className="space-y-3">
      <Card>
        <CardHeader className="pb-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <CardTitle className="text-base">文献阅读</CardTitle>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span>左：清单 · 中：原文 · 右：入库计划与批注</span>
              <Button size="sm" variant="outline" className="h-7 px-2 text-xs"
                      onClick={() => toast.info('上传入口将在下一轮接入（当前可从科研知识库添加文献）')}>
                <Upload className="mr-1 h-3 w-3" />上传 PDF
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          <div className="grid min-h-[600px] grid-cols-1 border-t border-border
                          lg:grid-cols-[260px_minmax(0,1fr)_320px]">
            {/* 左：阅读清单 */}
            <div className="hidden border-r border-border lg:block">
              {/* R2-k：外部上传（先读后入库） */}
              <div className="border-b border-border p-2">
                <label className="flex cursor-pointer items-center justify-center gap-1
                                  rounded-md border border-dashed border-border px-2 py-2
                                  text-[11px] text-muted-foreground hover:bg-accent">
                  {uploading
                    ? <Loader2 className="h-3 w-3 animate-spin" />
                    : <Upload className="h-3 w-3" />}
                  {uploading ? '上传中…' : '上传 PDF 到这里阅读（暂不入库）'}
                  <input type="file" accept="application/pdf" className="hidden"
                         onChange={(e) => void uploadPdf(e.target.files?.[0])} />
                </label>
                {inbox.length > 0 && (
                  <ul className="mt-1.5 space-y-1">
                    {inbox.map((it) => (
                      <li key={it.inbox_id}
                          className={cn('flex items-center justify-between gap-1 rounded-md px-2 py-1 text-[11px]',
                                        inboxId === it.inbox_id ? 'bg-primary/10' : 'hover:bg-accent/60')}>
                        <button type="button" className="min-w-0 flex-1 truncate text-left"
                                title={it.filename}
                                onClick={() => { setInboxId(it.inbox_id); setPaperId('');
                                                 setPlan(null); setAtts([]); setAnnos([]); }}>
                          {it.filename}
                          <span className="ml-1 text-muted-foreground">待入库</span>
                        </button>
                        <button type="button" title="从清单移除"
                                onClick={() => void discardInbox(it.inbox_id)}
                                className="shrink-0 text-muted-foreground hover:text-destructive">
                          <Trash2 className="h-3 w-3" />
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <div className="flex flex-wrap gap-1 border-b border-border p-2">
                {STATES.map((s) => (
                  <button key={s.key || 'all'} type="button"
                          onClick={() => setState(s.key)}
                          className={cn(
                            'rounded-full border px-2 py-0.5 text-[11px] transition-colors',
                            state === s.key ? 'border-primary bg-primary/10 text-primary'
                                            : 'border-border hover:bg-accent',
                          )}>
                    {s.label}
                  </button>
                ))}
              </div>
              <div className="max-h-[560px] overflow-y-auto">
                {loading && (
                  <p className="p-3 text-center text-xs text-muted-foreground">
                    <Loader2 className="mx-auto h-4 w-4 animate-spin" />
                  </p>
                )}
                {!loading && queue.length === 0 && (
                  <p className="p-3 text-center text-xs text-muted-foreground">清单为空</p>
                )}
                {queue.map((r) => (
                  <button key={r.paper_id} type="button" onClick={() => void open(r.paper_id)}
                          className={cn(
                            'flex w-full flex-col items-start gap-1 border-b border-border/60 px-3 py-2 text-left transition-colors',
                            paperId === r.paper_id ? 'bg-primary/5' : 'hover:bg-accent/50',
                          )}>
                    <span className="line-clamp-2 text-xs">{r.title || '（无标题）'}</span>
                    <span className="flex flex-wrap items-center gap-1">
                      <Badge variant="outline" className="text-[10px]">
                        {STATE_LABEL[r.reading_state] ?? r.reading_state}
                      </Badge>
                      {r.parse_status === 'parsed' ? (
                        <Badge variant="outline" className="border-primary/40 text-[10px] text-primary">
                          已解析
                        </Badge>
                      ) : (
                        <Badge variant="outline" className="text-[10px] text-muted-foreground">
                          {r.parse_status === 'partial' ? '部分解析' : '未解析'}
                          {r.missing_parts?.length ? ` · 缺 ${r.missing_parts.length}` : ''}
                        </Badge>
                      )}
                      {r.annotations > 0 && (
                        <span className="text-[10px] text-muted-foreground">
                          批注 {r.annotations}
                        </span>
                      )}
                    </span>
                  </button>
                ))}
              </div>
            </div>

            {/* 中：PDF */}
            <div className="min-w-0 border-r border-border">
              {inboxId ? (
                <PdfViewer src={`/api/library/reading/inbox/${inboxId}/file`}
                           annotations={[]}
                           onSelectText={(t, p, pos) => {
                             setSelText(t); setAnnoPage(String(p)); setSelPos(pos);
                           }} />
              ) : !paperId ? (
                <p className="mt-24 text-center text-sm text-muted-foreground">
                  <BookOpen className="mx-auto mb-2 h-6 w-6" />
                  从左侧清单选择一篇文献开始阅读
                </p>
              ) : !main ? (
                <p className="mt-24 text-center text-sm text-muted-foreground">
                  该文献暂无 PDF 附件
                  <br />
                  <span className="text-xs">
                    可在「科研知识库 → 添加文献」上传主文与 SI 后再来阅读
                  </span>
                </p>
              ) : (
                <PdfViewer src={`/api/literature/attachments/${main.file_id}/file`}
                           annotations={annos}
                           onSelectText={(t, p, pos) => {
                             setSelText(t); setAnnoPage(String(p)); setSelPos(pos);
                           }} />
              )}
            </div>

            {/* 右：入库计划 + 阅读状态 + 批注 */}
            <div className="min-w-0 p-3">
              {inboxId && !plan ? (
                <div className="space-y-2 text-[11px] text-muted-foreground">
                  <p className="text-xs font-medium text-foreground">尚未入库的外部文献</p>
                  <p>这份 PDF 只在阅读清单里（不占库编号）。读完后入库会走「添加文献 → 解析」流程，
                     生成条目并把解析结果写入科研知识库。</p>
                  <p className="rounded-md bg-muted/30 p-2">
                    点下方按钮即可正式入库：自动抽元数据 → 创建条目（编号 1782+）→ 挂上主文附件。
                    入库后在右栏点「提取解析并入库」完成条目/图片抽取。
                  </p>
                  <Button size="sm" className="h-7 text-xs" disabled={ingesting}
                          onClick={() => void ingestInboxItem()}>
                    {ingesting
                      ? <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                      : <FileText className="mr-1 h-3 w-3" />}
                    {ingesting ? '入库中…' : '读完入库（建条目 + 挂附件）'}
                  </Button>
                </div>
              ) : !plan ? (
                <p className="mt-8 text-center text-xs text-muted-foreground">
                  选择文献后显示解析状态与入库动作
                </p>
              ) : (
                <div className="space-y-3">
                  <div className="rounded-lg border border-border/70 p-2.5">
                    <p className="text-xs font-medium">解析状态</p>
                    <p className="mt-1 text-[11px] text-muted-foreground">
                      摘要 {plan.counts?.has_abstract ? '✓' : '✗'} · 条目 {plan.counts?.entries ?? 0} ·
                      图片 {plan.counts?.figures ?? 0}
                    </p>
                    {plan.missing_parts?.length > 0 ? (
                      <p className="mt-1 text-[11px] text-muted-foreground">
                        缺失：{plan.missing_parts.map((m) => PART_LABEL[m] ?? m).join('、')}
                      </p>
                    ) : (
                      <p className="mt-1 flex items-center gap-1 text-[11px] text-primary">
                        <CheckCircle2 className="h-3 w-3" />三项齐备
                      </p>
                    )}
                    <Button size="sm" className="mt-2 h-7 text-xs"
                            disabled={ingesting}
                            onClick={() => void runIngest()}>
                      {ingesting
                        ? <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                        : <FileText className="mr-1 h-3 w-3" />}
                      {ingesting ? '解析中…（可能数分钟）'
                                 : plan.full_parse ? '提取解析并入库' : '补全缺失解析'}
                    </Button>
                  </div>

                  <div className="rounded-lg border border-border/70 p-2.5">
                    <p className="text-xs font-medium">阅读状态</p>
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {(['unread', 'reading', 'read', 'ingested'] as const).map((s) => (
                        <button key={s} type="button" onClick={() => void setReadingState(s)}
                                className={cn(
                                  'rounded-full border px-2 py-0.5 text-[11px]',
                                  plan.reading_state === s
                                    ? 'border-primary bg-primary/10 text-primary'
                                    : 'border-border hover:bg-accent',
                                )}>
                          {STATE_LABEL[s]}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div className="rounded-lg border border-border/70 p-2.5">
                    <p className="flex items-center gap-1 text-xs font-medium">
                      <Languages className="h-3.5 w-3.5" />翻译 / 提问（DeepSeek）
                    </p>
                    <Textarea value={selText} rows={3} className="mt-1.5 text-xs"
                              onChange={(e) => setSelText(e.target.value)}
                              placeholder="粘贴或选中的原文段落（下一轮接入 PDF 划选自动填入）…" />
                    <div className="mt-1.5 flex items-center gap-1">
                      <Button size="sm" className="h-7 text-xs" disabled={busy || !selText.trim()}
                              onClick={() => void doTranslate()}>
                        {busy ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
                        翻译为中文
                      </Button>
                      <span className="text-[10px] text-muted-foreground">
                        术语与 DOI/SMILES/数字自动保护；同段二次翻译走缓存
                      </span>
                    </div>
                    {translation && (
                      <p className="mt-1.5 whitespace-pre-wrap break-words rounded-md bg-muted/30 p-2 text-[11px]">
                        {translation}
                      </p>
                    )}

                    <div className="mt-2.5 border-t border-border/60 pt-2">
                      <Input value={question} className="h-7 text-xs"
                             onChange={(e) => setQuestion(e.target.value)}
                             onKeyDown={(e) => { if (e.key === 'Enter') void doAsk(); }}
                             placeholder="就这篇文献提问（依据摘要 / 条目 / 选中段落）…" />
                      <Button size="sm" variant="outline" className="mt-1.5 h-7 text-xs"
                              disabled={busy || !question.trim()}
                              onClick={() => void doAsk()}>
                        提问
                      </Button>
                      {answer && (
                        <p className="mt-1.5 whitespace-pre-wrap break-words rounded-md bg-muted/30 p-2 text-[11px]">
                          {answer}
                        </p>
                      )}
                    </div>
                  </div>

                  <div className="rounded-lg border border-border/70 p-2.5">
                    <p className="flex items-center gap-1 text-xs font-medium">
                      <NotebookPen className="h-3.5 w-3.5" />批注（{annos.length}）
                    </p>
                    {annos.length === 0 ? (
                      <p className="mt-1 text-[11px] text-muted-foreground">
                        暂无批注（高亮/便签/截图将在下一轮接入阅读器；当前可手动记录）
                      </p>
                    ) : (
                      <ul className="mt-1 space-y-1">
                        {annos.map((a) => (
                          <li key={a.annotation_id}
                              className="flex items-start justify-between gap-1 text-[11px] text-muted-foreground">
                            <span className="min-w-0 flex-1">
                              第 {a.page ?? '-'} 页 · {a.text || a.comment || a.type}
                            </span>
                            <button type="button" title="删除批注"
                                    onClick={() => void removeAnnotation(a.annotation_id)}
                                    className="shrink-0 hover:text-destructive">
                              <Trash2 className="h-3 w-3" />
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                    {/* R2-g：手动记批注（页码 / 颜色 / 评论；原文片段复用上方选中框） */}
                    <div className="mt-2 space-y-1.5 border-t border-border/60 pt-2">
                      <div className="flex items-center gap-1">
                        <Input value={annoPage} onChange={(e) => setAnnoPage(e.target.value)}
                               className="h-7 w-16 text-xs" placeholder="页码" />
                        <select value={annoColor} onChange={(e) => setAnnoColor(e.target.value)}
                                className="h-7 rounded-md border border-input bg-background px-1 text-[11px]">
                          <option value="yellow">黄</option>
                          <option value="green">绿</option>
                          <option value="blue">蓝</option>
                          <option value="red">红</option>
                        </select>
                        <Input value={annoComment} onChange={(e) => setAnnoComment(e.target.value)}
                               onKeyDown={(e) => { if (e.key === 'Enter') void addAnnotation(); }}
                               className="h-7 flex-1 text-xs" placeholder="评论（可选）" />
                      </div>
                      <Button size="sm" className="h-7 text-xs"
                              disabled={!selText.trim() && !annoComment.trim()}
                              onClick={() => void addAnnotation()}>
                        添加批注（原文用上方选中框）
                      </Button>
                    </div>
                    <Button size="sm" variant="outline" className="mt-2 h-7 text-xs"
                            disabled={annos.length === 0} onClick={() => void exportNote()}>
                      批注导出为笔记
                    </Button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
