/**
 * 「科研知识库」文献卡片（v1.9.0）：文献列表 + 每篇文献的分组条目 / 图谱 /
 * 补解析（LLM 全维度提取，未配置降级正则扫描）。
 *
 * 与 LiteratureIntakeSection（录入）同属统一「科研知识库」Section：
 * 本组件负责「已入库文献 → 结构化知识」的浏览与扩充。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import {
  BookOpen, FlaskConical, FileUp, Loader2, Pencil,
  Play, RefreshCw, Trash2,
} from 'lucide-react';
import { toast } from 'sonner';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Textarea } from '@/components/ui/textarea';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { LiteratureFiguresPanel } from './LiteratureFiguresPanel';

const BASE = '/api/literature';

// ---------- 类型 ----------

interface PaperMeta {
  paper_id: string;
  title: string;
  doi: string;
  journal: string;
  year?: number | null;
}

interface Entry {
  entry_id: string;
  paper_id: string;
  group_id: string;
  experiment: string;
  kind: string;
  film_label?: number;
  technique?: string;
  metrics?: { name: string; value: number; unit?: string }[];
  ald_smiles?: string;
  amine_smiles?: string;
  evidence: string;
  conclusion?: string;
  graph_indexed?: boolean;
  source?: string;
  conditions?: Record<string, string>;
}

interface ParsePreview {
  llm_used: boolean;
  note: string;
  entries: Partial<Entry>[];
}

const KIND_LABEL: Record<string, string> = {
  monomer: '单体', monomer_pair: '单体对', film_outcome: '成膜结论',
  condition: '合成条件', characterization: '表征', property: '性能',
  conclusion: '结论', dft: 'DFT 计算',
};

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: init?.body && !(init.body instanceof FormData)
        ? { 'Content-Type': 'application/json' }
        : undefined,
      ...init,
    });
  } catch {
    toast.error('无法连接后端服务');
    throw new Error('backend-unavailable');
  }
  if (!res.ok) {
    let message = `请求失败（${res.status}）`;
    try {
      const data = await res.json();
      if (typeof data?.detail === 'string') message = data.detail;
    } catch {
      /* keep */
    }
    toast.error(message);
    throw new Error(message);
  }
  return (await res.json()) as T;
}

function EntryBadge({ e }: { e: Partial<Entry> }) {
  if (e.kind === 'film_outcome' && e.film_label != null) {
    const cls = e.film_label >= 1
      ? 'border-emerald-400 bg-emerald-50 text-emerald-700'
      : e.film_label >= 0.5
        ? 'border-amber-400 bg-amber-50 text-amber-700'
        : 'border-red-400 bg-red-50 text-red-700';
    const text = e.film_label >= 1 ? '成膜' : e.film_label >= 0.5 ? '边界' : '不成膜';
    return <Badge variant="outline" className={cls}>{text} {e.film_label}</Badge>;
  }
  return <Badge variant="outline">{KIND_LABEL[e.kind ?? ''] ?? e.kind}</Badge>;
}

// ---------- 主组件 ----------

export function LiteratureKnowledgeSection() {
  const navigate = useNavigate();
  const [papers, setPapers] = useState<PaperMeta[]>([]);
  const [filter, setFilter] = useState('');
  const [paperId, setPaperId] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [entriesLoading, setEntriesLoading] = useState(false);

  // 补解析
  const [parseOpen, setParseOpen] = useState(false);
  const [parseBusy, setParseBusy] = useState(false);
  const [parseText, setParseText] = useState('');
  const [preview, setPreview] = useState<ParsePreview | null>(null);
  const [checked, setChecked] = useState<Record<number, boolean>>({});
  const pdfRef = useRef<HTMLInputElement>(null);

  // 编辑/删除
  const [editTarget, setEditTarget] = useState<Entry | null>(null);
  const [editJson, setEditJson] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<Entry | null>(null);

  const loadPapers = useCallback(async (selectId?: string) => {
    try {
      const data = await req<{ papers: PaperMeta[] }>('/papers');
      setPapers(data.papers ?? []);
      const list = data.papers ?? [];
      const target = selectId ?? list[0]?.paper_id ?? '';
      if (target) setPaperId(target);
      return target;
    } catch {
      return '';
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadPapers();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadEntries = useCallback(async (pid: string) => {
    setEntriesLoading(true);
    try {
      const data = await req<{ entries: Entry[] }>(
        `/${encodeURIComponent(pid)}/entries`);
      setEntries(data.entries ?? []);
    } catch {
      setEntries([]);
    } finally {
      setEntriesLoading(false);
    }
  }, []);

  useEffect(() => {
    if (paperId) void loadEntries(paperId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paperId]);

  const runParse = async (file?: File) => {
    setParseBusy(true);
    setPreview(null);
    try {
      const form = new FormData();
      if (file) form.append('file', file);
      else if (parseText.trim()) form.append('text', parseText.trim());
      const data = await req<ParsePreview>(
        `/${encodeURIComponent(paperId)}/parse`,
        { method: 'POST', body: form });
      setPreview(data);
      setChecked(Object.fromEntries(
        data.entries.map((_, i) => [i, true])));
      if (data.entries.length === 0) {
        toast.warning('未提取到条目：可检查全文或配置文献解析 LLM');
      }
    } catch {
      /* 已 toast */
    } finally {
      setParseBusy(false);
    }
  };

  const submitParse = async () => {
    if (!preview) return;
    const chosen = preview.entries.filter((_, i) => checked[i]);
    if (chosen.length === 0) {
      toast.error('请至少勾选一条条目');
      return;
    }
    setParseBusy(true);
    try {
      await req(`/${encodeURIComponent(paperId)}/entries`, {
        method: 'POST',
        body: JSON.stringify({ entries: chosen }),
      });
      toast.success(`已入库 ${chosen.length} 条并同步知识图谱`);
      setParseOpen(false);
      await loadEntries(paperId);
    } catch {
      /* 已 toast */
    } finally {
      setParseBusy(false);
    }
  };

  const saveEdit = async () => {
    if (!editTarget) return;
    try {
      const parsed = JSON.parse(editJson) as Record<string, unknown>;
      await req(`/entries/${encodeURIComponent(editTarget.entry_id)}`, {
        method: 'PATCH', body: JSON.stringify({ entry: parsed }),
      });
      toast.success('条目已更新（图谱已同步）');
      setEditTarget(null);
      await loadEntries(paperId);
    } catch (e) {
      toast.error(e instanceof SyntaxError ? 'JSON 格式错误' : e instanceof Error ? e.message : '更新失败');
    }
  };

  const doDelete = async () => {
    if (!deleteTarget) return;
    try {
      await req(`/entries/${encodeURIComponent(deleteTarget.entry_id)}`, {
        method: 'DELETE',
      });
      toast.success('条目已删除（图谱已同步）');
      setDeleteTarget(null);
      await loadEntries(paperId);
    } catch {
      /* 已 toast */
    }
  };

  const gotoQuery = (e: Entry) => {
    const a = encodeURIComponent(e.ald_smiles ?? '');
    const b = encodeURIComponent(e.amine_smiles ?? '');
    navigate(`/toolbox/query?a=${a}&b=${b}`);
  };

  const gotoDft = (e: Entry) => {
    const a = encodeURIComponent(e.ald_smiles ?? '');
    const b = encodeURIComponent(e.amine_smiles ?? '');
    navigate(`/toolbox/dft?a=${a}&b=${b}`);
  };

  const toGnn = async (e: Entry) => {
    try {
      await req(`/entries/${encodeURIComponent(e.entry_id)}/to-gnn-feedback`, {
        method: 'POST',
      });
      toast.success('已加入 GNN 反馈队列（设置 → GNN 模型演进 可见）');
    } catch {
      /* 已 toast */
    }
  };

  const filtered = papers.filter((p) =>
    !filter.trim() || p.title.toLowerCase().includes(filter.trim().toLowerCase())
    || String(p.paper_id) === filter.trim());

  const groups: [string, Entry[]][] = [];
  for (const e of entries) {
    const g = groups.find(([gid]) => gid === e.group_id);
    if (g) g[1].push(e);
    else groups.push([e.group_id, [e]]);
  }

  const paper = papers.find((p) => p.paper_id === paperId);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-lg">
          <BookOpen className="h-4 w-4 text-gold" />
          科研知识库（结构化条目 · 图谱 · 补解析）
        </CardTitle>
      </CardHeader>
      <CardContent className="p-4 pt-2">
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> 加载中…
          </div>
        ) : papers.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            文献库为空：先在上方「文献录入」入库文献。
          </p>
        ) : (
          <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
            {/* 文献列表 */}
            <div className="space-y-2">
              <input
                className="h-8 w-full rounded-md border border-input bg-transparent px-2 text-sm"
                placeholder="筛选文献（标题 / #编号）"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              />
              <div className="max-h-[60vh] space-y-1 overflow-y-auto pr-1">
                {filtered.map((p) => (
                  <button
                    key={p.paper_id}
                    type="button"
                    onClick={() => setPaperId(p.paper_id)}
                    className={`block w-full rounded-lg border px-2.5 py-1.5 text-left text-xs ${
                      p.paper_id === paperId
                        ? 'border-gold bg-gold-muted/40'
                        : 'border-transparent hover:bg-muted/50'
                    }`}
                  >
                    <span className="block truncate font-medium text-foreground">
                      #{p.paper_id} {p.title.slice(0, 30)}
                      {p.title.length > 30 ? '…' : ''}
                    </span>
                    <span className="block truncate text-muted-foreground">
                      {[p.journal, p.year, p.doi ? p.doi.slice(0, 24) : '']
                        .filter(Boolean).join(' · ')}
                    </span>
                  </button>
                ))}
              </div>
            </div>

            {/* 选中文献卡片 */}
            <div className="min-w-0 space-y-3">
              {!paper ? (
                <p className="text-sm text-muted-foreground">选择左侧文献查看。</p>
              ) : (
                <>
                  <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-muted/30 px-3 py-2">
                    <p className="min-w-0 flex-1 text-sm">
                      <span className="font-medium">#{paper.paper_id}</span>{' '}
                      {paper.title}
                    </p>
                    {paper.doi && (
                      <span className="text-xs text-muted-foreground">
                        {paper.doi}
                      </span>
                    )}
                    <Button size="sm" variant="outline"
                            onClick={() => {
                              setParseText('');
                              setPreview(null);
                              setParseOpen(true);
                            }}>
                      <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                      补解析
                    </Button>
                  </div>

                  <Tabs defaultValue="entries">
                    <TabsList>
                      <TabsTrigger value="entries">
                        结构化条目（{entries.length}）
                      </TabsTrigger>
                      <TabsTrigger value="figures">图谱</TabsTrigger>
                    </TabsList>

                    <TabsContent value="entries" className="space-y-3 pt-2">
                      {entriesLoading ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : entries.length === 0 ? (
                        <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
                          尚无结构化条目：点右上「补解析」用 LLM 提取文献中的
                          单体/成膜体系/条件/表征（未配置解析 LLM 时降级 SMILES 扫描）。
                        </p>
                      ) : (
                        groups.map(([gid, rows]) => (
                          <div key={gid} className="rounded-lg border border-border">
                            <div className="flex items-center gap-2 border-b border-border bg-muted/40 px-3 py-1.5">
                              <Badge variant="outline" className="border-gold text-gold-foreground">
                                组 {gid}
                              </Badge>
                              <span className="truncate text-xs text-muted-foreground"
                                    title={rows[0]?.experiment}>
                                {rows[0]?.experiment || '（无组描述）'}
                              </span>
                            </div>
                            <div className="divide-y divide-border">
                              {rows.map((e) => (
                                <div key={e.entry_id}
                                     className="flex flex-wrap items-start gap-2 px-3 py-2 text-xs">
                                  <EntryBadge e={e} />
                                  {e.technique && (
                                    <Badge variant="outline">{e.technique}</Badge>
                                  )}
                                  <span className="min-w-0 flex-1">
                                    {e.metrics?.map((m) => (
                                      <span key={m.name}
                                            className="mr-2 inline-block rounded bg-muted/60 px-1.5 py-0.5">
                                        {m.name} {m.value}{m.unit}
                                      </span>
                                    ))}
                                    {e.conclusion && (
                                      <span className="mr-2 text-muted-foreground">
                                        {e.conclusion}
                                      </span>
                                    )}
                                    <span className="block truncate text-muted-foreground/70"
                                          title={e.evidence}>
                                      依据：{e.evidence}
                                    </span>
                                  </span>
                                  <span className="flex shrink-0 items-center gap-1">
                                    {e.ald_smiles && e.amine_smiles && (
                                      <>
                                        <Button size="sm" variant="outline"
                                                className="h-6 px-1.5 text-[11px]"
                                                title="导入成膜打分验证"
                                                onClick={() => gotoQuery(e)}>
                                          <Play className="mr-1 h-3 w-3" />打分
                                        </Button>
                                        <Button size="sm" variant="outline"
                                                className="h-6 px-1.5 text-[11px]"
                                                title="本机 DFT 重算对照"
                                                onClick={() => gotoDft(e)}>
                                          <FlaskConical className="mr-1 h-3 w-3" />DFT
                                        </Button>
                                      </>
                                    )}
                                    {e.kind === 'film_outcome' && (
                                      <Button size="sm" variant="outline"
                                              className="h-6 px-1.5 text-[11px]"
                                              title="加入 GNN 反馈队列（成膜结论）"
                                              onClick={() => void toGnn(e)}>
                                        GNN反馈
                                      </Button>
                                    )}
                                    <button type="button" title="编辑（JSON）"
                                            className="rounded p-1 text-muted-foreground hover:text-foreground"
                                            onClick={() => {
                                              setEditTarget(e);
                                              setEditJson(JSON.stringify(e, null, 2));
                                            }}>
                                      <Pencil className="h-3 w-3" />
                                    </button>
                                    <button type="button" title="删除条目"
                                            className="rounded p-1 text-muted-foreground hover:text-destructive"
                                            onClick={() => setDeleteTarget(e)}>
                                      <Trash2 className="h-3 w-3" />
                                    </button>
                                  </span>
                                </div>
                              ))}
                            </div>
                          </div>
                        ))
                      )}
                    </TabsContent>

                    <TabsContent value="figures" className="pt-2">
                      <LiteratureFiguresPanel fixedPaperId={paperId} />
                    </TabsContent>
                  </Tabs>
                </>
              )}
            </div>
          </div>
        )}

        {/* 补解析弹窗 */}
        <Dialog open={parseOpen} onOpenChange={(o) => !o && !parseBusy && setParseOpen(false)}>
          <DialogContent className="max-w-2xl">
            <DialogHeader>
              <DialogTitle>补解析：LLM 全维度提取（#{paperId}）</DialogTitle>
            </DialogHeader>
            {!preview ? (
              <div className="space-y-3">
                <input
                  ref={pdfRef}
                  type="file"
                  accept=".pdf"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    e.target.value = '';
                    if (f) void runParse(f);
                  }}
                />
                <Textarea
                  value={parseText}
                  onChange={(e) => setParseText(e.target.value)}
                  rows={6}
                  placeholder="粘贴文献全文（或直接上传 PDF）——解析 LLM 未配置时降级为 SMILES 正则扫描"
                />
                <DialogFooter className="gap-2">
                  <Button variant="outline"
                          onClick={() => pdfRef.current?.click()}
                          disabled={parseBusy}>
                    {parseBusy
                      ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                      : <FileUp className="mr-1.5 h-4 w-4" />}
                    上传 PDF 解析
                  </Button>
                  <Button onClick={() => void runParse()}
                          disabled={parseBusy || !parseText.trim()}>
                    {parseBusy ? '解析中…' : '用全文文本解析'}
                  </Button>
                </DialogFooter>
              </div>
            ) : (
              <div className="space-y-3">
                <p className="text-xs text-muted-foreground">
                  {preview.llm_used ? 'LLM 结构化提取' : 'SMILES 正则扫描（降级）'}：
                  {preview.note}
                  （勾选要入库的条目，按组归类）
                </p>
                <div className="max-h-[50vh] space-y-2 overflow-y-auto">
                  {Object.entries(
                    preview.entries.reduce<Record<string, (Partial<Entry> & { idx: number })[]>>(
                      (acc, e, i) => {
                        const g = String(e.group_id ?? '未分组');
                        (acc[g] ??= []).push({ ...e, idx: i });
                        return acc;
                      }, {})).map(([gid, rows]) => (
                    <div key={gid} className="rounded-lg border border-border">
                      <p className="border-b border-border bg-muted/40 px-2 py-1 text-xs font-medium">
                        组 {gid}：{rows[0]?.experiment}
                      </p>
                      {rows.map((e) => (
                        <label key={e.idx}
                               className="flex cursor-pointer items-start gap-2 px-2 py-1.5 text-xs hover:bg-muted/40">
                          <input
                            type="checkbox"
                            className="mt-0.5"
                            checked={checked[e.idx] !== false}
                            onChange={(ev) =>
                              setChecked((c) => ({ ...c, [e.idx]: ev.target.checked }))}
                          />
                          <span className="min-w-0 flex-1">
                            <EntryBadge e={e} />
                            {e.technique && (
                              <span className="ml-1 text-muted-foreground">
                                {e.technique}
                              </span>
                            )}
                            <span className="ml-1 text-muted-foreground">
                              {e.metrics?.map((m) => `${m.name}=${m.value}${m.unit ?? ''}`).join('，')}
                            </span>
                            <span className="mt-0.5 block truncate text-muted-foreground/70"
                                  title={e.evidence}>
                              依据：{e.evidence}
                            </span>
                          </span>
                        </label>
                      ))}
                    </div>
                  ))}
                </div>
                <DialogFooter>
                  <Button variant="outline"
                          onClick={() => { setPreview(null); setParseText(''); }}>
                    重新解析
                  </Button>
                  <Button onClick={() => void submitParse()} disabled={parseBusy}>
                    {parseBusy ? '入库中…' : '勾选条目入库（同步知识图谱）'}
                  </Button>
                </DialogFooter>
              </div>
            )}
          </DialogContent>
        </Dialog>

        {/* 编辑条目（JSON 高级编辑） */}
        <Dialog open={editTarget !== null} onOpenChange={(o) => !o && setEditTarget(null)}>
          <DialogContent className="max-w-xl">
            <DialogHeader>
              <DialogTitle>编辑条目（JSON）</DialogTitle>
            </DialogHeader>
            <Textarea
              value={editJson}
              onChange={(e) => setEditJson(e.target.value)}
              rows={16}
              className="font-mono text-xs"
            />
            <DialogFooter>
              <Button variant="outline" onClick={() => setEditTarget(null)}>取消</Button>
              <Button onClick={() => void saveEdit()}>保存并同步图谱</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* 删除确认 */}
        <Dialog open={deleteTarget !== null} onOpenChange={(o) => !o && setDeleteTarget(null)}>
          <DialogContent className="max-w-sm">
            <DialogHeader>
              <DialogTitle>确认删除该条目？</DialogTitle>
            </DialogHeader>
            <p className="text-sm text-muted-foreground">
              删除后同步撤出知识图谱（组内无剩余条目时整组移除）。不可恢复。
            </p>
            <DialogFooter>
              <Button variant="outline" onClick={() => setDeleteTarget(null)}>取消</Button>
              <Button className="bg-red-600 text-white hover:bg-red-700"
                      onClick={() => void doDelete()}>
                确认删除
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </CardContent>
    </Card>
  );
}
