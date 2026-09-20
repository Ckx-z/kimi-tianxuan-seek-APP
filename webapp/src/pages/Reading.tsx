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
         Upload } from 'lucide-react';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/badge';
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
        fetch(`/api/literature/papers/${encodeURIComponent(pid)}/attachments`)
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
              {!paperId ? (
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
                <iframe
                  title={`paper-${paperId}`}
                  src={`/api/literature/attachments/${main.file_id}/file`}
                  className="h-[600px] w-full bg-muted/20"
                />
              )}
            </div>

            {/* 右：入库计划 + 阅读状态 + 批注 */}
            <div className="min-w-0 p-3">
              {!plan ? (
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
                            onClick={() => toast.info(
                              plan.full_parse
                                ? '将执行全量解析（下一轮接入此处触发，当前可在知识库卡片「补解析」）'
                                : `将只补缺失项：${plan.parts.map((p) => PART_LABEL[p] ?? p).join('、')}`)}>
                      <FileText className="mr-1 h-3 w-3" />
                      {plan.full_parse ? '提取解析并入库' : '补全缺失解析'}
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
                        暂无批注（高亮/便签/截图将在下一轮接入阅读器）
                      </p>
                    ) : (
                      <ul className="mt-1 space-y-1">
                        {annos.map((a) => (
                          <li key={a.annotation_id} className="text-[11px] text-muted-foreground">
                            第 {a.page ?? '-'} 页 · {a.text || a.comment || a.type}
                          </li>
                        ))}
                      </ul>
                    )}
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
