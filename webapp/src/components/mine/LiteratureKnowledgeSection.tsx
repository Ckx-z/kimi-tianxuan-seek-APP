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
  BookOpen, FileText, FlaskConical, FileCheck2, FileUp, Loader2, Pencil,
  Play, RefreshCw, Trash2, X,
} from 'lucide-react';
import { toast } from 'sonner';
import { cn } from '@/lib/utils';
import {
  ATTACHMENT_MAX_PER_PAPER,
  attachmentUrl,
  deleteLiteratureAttachment,
  listLiteratureAttachments,
  updateLiteratureAttachmentRole,
  uploadLiteratureAttachments,
  type LiteratureAttachment,
} from './api';
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

interface PaperDetail {
  paper_id: string;
  title: string;
  authors: string[];
  journal: string;
  year?: number | null;
  doi: string;
  url: string;
  abstract?: string | null;
  source?: string;
  added_at?: string;
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
  /** v1.9.3：条目来源附件（[主文 xx.pdf] / [SI 1 xx.pdf]） */
  source_file?: string;
  /** v1.9.3（解析预览）：试校验结果 —— false 时不可勾选入库 */
  valid?: boolean;
  invalid_reason?: string;
  conditions?: Record<string, string>;
}

interface ParsePreview {
  llm_used: boolean;
  note: string;
  entries: Partial<Entry>[];
  /** v1.9.3：文献级元数据（LLM 提取；确认后可回填文献库，只补空字段） */
  paper_meta?: {
    title?: string;
    authors?: string[];
    journal?: string;
    year?: number;
    doi?: string;
    abstract?: string;
  };
  meta_note?: string;
  /** 本次解析范围（前端展示用） */
  chars?: number;
  pages?: number;
  segments?: { total: number; failed: number };
  /** v1.9.3：本次解析用了哪些附件（主文/SI） */
  sources?: { filename: string; role: string; pages: number; chars: number }[];
  /** 本次解析新留存的附件 */
  saved?: LiteratureAttachment[];
  save_errors?: { filename: string; message: string }[];
  /** 疑似扫描件（无文本层，被跳过） */
  scanned?: string[];
  /** v1.9.3：试校验通过/不通过条数（不通过的默认不勾选） */
  valid_count?: number;
  invalid_count?: number;
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
      ? 'border-success/40 bg-success/10 text-success'
      : e.film_label >= 0.5
        ? 'border-warning/40 bg-warning/10 text-warning'
        : 'border-destructive/40 bg-destructive/10 text-destructive';
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
  const [detail, setDetail] = useState<PaperDetail | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [entriesLoading, setEntriesLoading] = useState(false);
  // 图谱历史导入（v1.9.2：把随包图谱反应节点导入为条目，幂等）
  const [importing, setImporting] = useState(false);
  const [importStats, setImportStats] = useState<{
    imported: number; total_entries: number;
  } | null>(null);

  // 补解析
  const [parseOpen, setParseOpen] = useState(false);
  const [parseBusy, setParseBusy] = useState(false);
  const [parseText, setParseText] = useState('');
  const [preview, setPreview] = useState<ParsePreview | null>(null);
  const [checked, setChecked] = useState<Record<number, boolean>>({});
  /** v1.9.3：待解析的新上传文件（主文 + SI，可多份） */
  const [pendingPdfs, setPendingPdfs] = useState<{ file: File; role: 'main' | 'si' }[]>([]);
  const pdfRef = useRef<HTMLInputElement>(null);
  /** v1.9.3：文献附件（主文/SI）列表 */
  const [attachments, setAttachments] = useState<LiteratureAttachment[]>([]);
  const [attachBusy, setAttachBusy] = useState(false);
  const attachRef = useRef<HTMLInputElement>(null);
  /** v1.9.3：解析耗时秒数（长文献按段解析可能数分钟，给出进度感知） */
  const [parseElapsed, setParseElapsed] = useState(0);

  useEffect(() => {
    if (!parseBusy) {
      setParseElapsed(0);
      return;
    }
    const started = Date.now();
    const id = window.setInterval(
      () => setParseElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(id);
  }, [parseBusy]);

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

  const loadAttachments = useCallback(async (pid: string) => {
    try {
      setAttachments(await listLiteratureAttachments(pid));
    } catch {
      setAttachments([]);
    }
  }, []);

  useEffect(() => {
    if (paperId) {
      void loadEntries(paperId);
      void loadAttachments(paperId);
      // 加载原文元数据（老文献的结构化信息：作者/期刊/摘要等）
      req<PaperDetail>(`/papers/${encodeURIComponent(paperId)}`)
        .then(setDetail)
        .catch(() => setDetail(null));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paperId]);

  /**
   * 解析（v1.9.3 问题 2）：
   * - 有新增文件 → 多文件一次提交（后端按 主文/SI 留存并分别解析后合并）；
   * - 无新增文件 + useStored → 复用已存附件解析；
   * - 否则用粘贴的全文文本。
   */
  const runParse = async (opts: { file?: File; useStored?: boolean } = {}) => {
    setParseBusy(true);
    setPreview(null);
    try {
      const form = new FormData();
      const files = opts.file
        ? [{ file: opts.file, role: 'main' as const }]
        : pendingPdfs;
      let usedStored = false;
      if (files.length > 0) {
        files.forEach((f) => form.append('files', f.file));
        form.append('roles', files.map((f) => f.role).join(','));
      } else if (opts.useStored || attachments.length > 0) {
        form.append('use_stored', 'true');
        usedStored = true;
      } else if (parseText.trim()) {
        form.append('text', parseText.trim());
      } else {
        toast.error('请选择主文/补充信息 PDF，或粘贴全文文本');
        setParseBusy(false);
        return;
      }
      const data = await req<ParsePreview>(
        `/${encodeURIComponent(paperId)}/parse`,
        { method: 'POST', body: form });
      setPreview(data);
      // v1.9.3：默认只勾选**合法**条目（字段不全的条目会导致原子入库整批失败）
      setChecked(Object.fromEntries(
        data.entries.map((e, i) => [i, e.valid !== false])));
      setPendingPdfs([]);
      await loadAttachments(paperId);
      const invalid = data.invalid_count
        ?? data.entries.filter((e) => e.valid === false).length;
      if (data.scanned?.length) {
        toast.warning(`以下 PDF 无可提取文本层（疑似扫描件）：${data.scanned.join('、')}`);
      } else if (data.entries.length === 0) {
        toast.warning('未提取到条目：可检查全文或配置文献解析 LLM');
      } else if (invalid > 0) {
        toast.warning(`提取 ${data.entries.length} 条，其中 ${invalid} 条字段不完整`
          + `已自动排除（字段完整的 ${data.entries.length - invalid} 条已勾选）`);
      } else if (usedStored) {
        toast.success(`已用 ${data.sources?.length ?? 0} 个已存附件解析`);
      }
    } catch {
      /* 已 toast */
    } finally {
      setParseBusy(false);
    }
  };

  /** 上传附件（知识库卡片上的「上传主文/SI」入口） */
  const uploadAttachments = async (files: FileList | File[]) => {
    const list = [...files];
    if (list.length === 0) return;
    setAttachBusy(true);
    try {
      const res = await uploadLiteratureAttachments(paperId, list);
      const roles = res.uploaded.map((u) => (u.role === 'main' ? '主文' : 'SI'));
      if (res.uploaded.length) {
        toast.success(`已上传 ${res.uploaded.length} 个附件（${roles.join('/')}）`);
      }
      res.errors.forEach((e) => toast.error(`${e.filename}：${e.message}`));
      await loadAttachments(paperId);
    } catch {
      /* 已 toast */
    } finally {
      setAttachBusy(false);
    }
  };

  const switchAttachmentRole = async (fileId: string, role: 'main' | 'si') => {
    try {
      await updateLiteratureAttachmentRole(fileId, role);
      toast.success(`已设为${role === 'main' ? '主文' : '补充信息（SI）'}`);
      await loadAttachments(paperId);
    } catch {
      /* 已 toast */
    }
  };

  const removeAttachment = async (fileId: string) => {
    try {
      await deleteLiteratureAttachment(fileId);
      toast.success('附件已删除');
      await loadAttachments(paperId);
    } catch {
      /* 已 toast */
    }
  };

  /** v1.9.3：把 LLM 提取的文献级元数据回填文献库（后端只补空字段） */
  const backfillMeta = async () => {
    const meta = preview?.paper_meta;
    if (!meta || Object.keys(meta).length === 0) return;
    setParseBusy(true);
    try {
      const res = await req<{
        updated: string[]; skipped: string[]; message: string;
      }>(`/papers/${encodeURIComponent(paperId)}`, {
        method: 'PATCH',
        body: JSON.stringify({ ...meta, only_empty: true }),
      });
      toast.success(res.message || '已回填文献库');
      const fresh = await req<PaperDetail>(`/papers/${encodeURIComponent(paperId)}`);
      setDetail(fresh);
    } catch {
      /* 已 toast */
    } finally {
      setParseBusy(false);
    }
  };

  const submitParse = async () => {
    if (!preview) return;
    const chosen = preview.entries.filter(
      (e, i) => e.valid !== false && checked[i] !== false);
    if (chosen.length === 0) {
      toast.error('请至少勾选一条字段完整的条目');
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

  const runGraphImport = async () => {
    setImporting(true);
    try {
      const stats = await req<{
        graph_nodes: number; papers: number;
        imported: number; total_entries: number;
      }>('/entries/import-from-graph', { method: 'POST' });
      setImportStats(stats);
      toast.success(
        stats.imported > 0
          ? `已导入 ${stats.imported} 条历史图谱条目（共 ${stats.total_entries} 条）`
          : '历史图谱条目已全部导入（幂等，无需重复）');
      if (paperId) await loadEntries(paperId);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '导入失败');
    } finally {
      setImporting(false);
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
        <CardTitle className="flex flex-wrap items-center justify-between gap-2 text-lg">
          <span className="flex items-center gap-2">
            <BookOpen className="h-4 w-4 text-gold" />
            科研知识库（结构化条目 · 图谱 · 补解析）
          </span>
          <Button
            size="sm"
            variant="outline"
            disabled={importing}
            onClick={() => void runGraphImport()}
            title="把随包知识图谱（5713 篇文献的反应节点）导入为结构化条目（幂等）"
          >
            {importing
              ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
              : <Play className="mr-1.5 h-3.5 w-3.5" />}
            导入图谱历史条目
          </Button>
        </CardTitle>
        {importStats && (
          <p className="text-xs text-muted-foreground">
            图谱历史导入：新增 {importStats.imported} 条 · 当前共 {importStats.total_entries} 条
          </p>
        )}
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
                    <div className="min-w-0 flex-1">
                      <p className="text-sm">
                        <span className="font-medium">#{paper.paper_id}</span>{' '}
                        {paper.title}
                      </p>
                      <p className="truncate text-xs text-muted-foreground">
                        {[paper.journal, paper.year, paper.doi]
                          .filter(Boolean).join(' · ') || '（元数据见「原文信息」页）'}
                      </p>
                    </div>
                    <Button size="sm" variant="outline"
                            onClick={() => {
                              setParseText('');
                              setPreview(null);
                              setPendingPdfs([]);
                              setParseOpen(true);
                            }}>
                      <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                      补解析
                    </Button>
                  </div>

                  {/* v1.9.3：附件（主文 + 补充信息 SI）——可随时补传/复用 */}
                  <div className="flex flex-wrap items-center gap-2 rounded-lg border border-dashed border-border px-3 py-2">
                    <input
                      ref={attachRef}
                      type="file"
                      accept=".pdf"
                      multiple
                      className="hidden"
                      onChange={(e) => {
                        const picked = [...(e.target.files ?? [])];
                        e.target.value = '';
                        if (picked.length) void uploadAttachments(picked);
                      }}
                    />
                    <span className="text-xs text-muted-foreground">
                      附件（主文/SI）：{attachments.length}/{ATTACHMENT_MAX_PER_PAPER}
                    </span>
                    {attachments.map((a) => (
                      <span key={a.file_id}
                            className="inline-flex items-center gap-1 rounded border border-border bg-muted/40 px-1.5 py-0.5 text-[11px]">
                        <Badge variant={a.role === 'main' ? 'default' : 'outline'}
                               className="text-[10px]">
                          {a.role === 'main' ? '主文' : 'SI'}
                        </Badge>
                        <a className="max-w-40 truncate hover:underline"
                           href={attachmentUrl(a.file_id)} target="_blank"
                           rel="noreferrer" title={a.filename}>
                          {a.filename}
                        </a>
                        <span className="text-muted-foreground/70">
                          {a.pages}页{a.chars === 0 ? '·无文本层' : ''}
                        </span>
                      </span>
                    ))}
                    <Button size="sm" variant="ghost" className="h-7 px-2 text-xs"
                            disabled={attachBusy
                              || attachments.length >= ATTACHMENT_MAX_PER_PAPER}
                            onClick={() => attachRef.current?.click()}>
                      {attachBusy
                        ? <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                        : <FileUp className="mr-1 h-3 w-3" />}
                      上传主文/SI
                    </Button>
                  </div>

                  <Tabs defaultValue="meta">
                    <TabsList>
                      <TabsTrigger value="meta">原文信息</TabsTrigger>
                      <TabsTrigger value="entries">
                        结构化条目（{entries.length}）
                      </TabsTrigger>
                      <TabsTrigger value="figures">图谱</TabsTrigger>
                    </TabsList>

                    {/* 原文信息：老文献自带的结构化元数据（非 LLM 提取条目） */}
                    <TabsContent value="meta" className="space-y-2 pt-2">
                      {detail ? (
                        <div className="space-y-2 rounded-lg border border-border bg-muted/20 p-3 text-sm">
                          <p className="font-medium">{detail.title}</p>
                          {detail.authors.length > 0 && (
                            <p className="text-muted-foreground">
                              作者：{detail.authors.join('；')}
                            </p>
                          )}
                          <p className="text-muted-foreground">
                            {[detail.journal, detail.year].filter(Boolean).join(' · ') || '期刊信息缺失'}
                          </p>
                          {detail.doi && (
                            <p className="text-muted-foreground">
                              DOI：{' '}
                              <a
                                href={detail.url || `https://doi.org/${detail.doi}`}
                                target="_blank"
                                rel="noreferrer"
                                className="text-primary underline-offset-2 hover:underline"
                              >
                                {detail.doi}
                              </a>
                            </p>
                          )}
                          {detail.abstract ? (
                            <p className="whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground">
                              {detail.abstract}
                            </p>
                          ) : (
                            <p className="rounded border border-dashed border-border px-2 py-1.5 text-xs text-muted-foreground">
                              该文献暂无摘要。点右上「补解析」上传主文 PDF（可加补充信息 SI），
                              即可同时提取摘要等元数据与结构化条目。
                            </p>
                          )}
                          {(detail.source || detail.added_at) && (
                            <p className="text-xs text-muted-foreground/70">
                              来源：{detail.source || '—'}
                              {detail.added_at ? ` · 入库 ${detail.added_at.slice(0, 10)}` : ''}
                            </p>
                          )}
                        </div>
                      ) : (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      )}
                    </TabsContent>

                    <TabsContent value="entries" className="space-y-3 pt-2">
                      {entriesLoading ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : entries.length === 0 ? (
                        <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
                          该文献尚未做结构化提取（原文元数据见「原文信息」页）。
                          可点右上「补解析」用 LLM 提取，或点左上「导入图谱
                          历史条目」把随包知识图谱的历史信息一键导入
                          （未配置解析 LLM 时降级 SMILES 扫描）。
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

        {/* 补解析弹窗（v1.9.4：高度受限 + 单一滚动区，底部按钮固定在弹窗底部永远可点） */}
        <Dialog open={parseOpen} onOpenChange={(o) => !o && !parseBusy && setParseOpen(false)}>
          <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-3">
            <DialogHeader className="shrink-0">
              <DialogTitle>补解析：LLM 全维度提取（#{paperId}）</DialogTitle>
            </DialogHeader>
            {!preview ? (
              <div className="space-y-3 overflow-y-auto">
                {/* v1.9.3：主文 + 多份补充信息（SI）一次选择；也可复用已存附件 */}
                <input
                  ref={pdfRef}
                  type="file"
                  accept=".pdf"
                  multiple
                  className="hidden"
                  onChange={(e) => {
                    const picked = [...(e.target.files ?? [])];
                    e.target.value = '';
                    if (picked.length === 0) return;
                    setPendingPdfs((prev) => {
                      const hasMain = attachments.some((a) => a.role === 'main')
                        || prev.some((p) => p.role === 'main');
                      return [...prev, ...picked.map((file, i) => ({
                        file,
                        role: (!hasMain && prev.length === 0 && i === 0)
                          ? 'main' as const : 'si' as const,
                      }))];
                    });
                  }}
                />

                {pendingPdfs.length > 0 && (
                  <div className="space-y-1 rounded-lg border border-border bg-muted/30 p-2">
                    <p className="text-xs text-muted-foreground">
                      待解析文件（{pendingPdfs.length}）：主文 1 份 + 补充信息可多份
                    </p>
                    {pendingPdfs.map((p, i) => (
                      <div key={`${p.file.name}-${i}`}
                           className="flex items-center gap-2 text-xs">
                        <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                        <span className="min-w-0 flex-1 truncate" title={p.file.name}>
                          {p.file.name}
                          <span className="ml-1 text-muted-foreground/70">
                            {(p.file.size / 1024 / 1024).toFixed(1)}MB
                          </span>
                        </span>
                        <button
                          type="button"
                          className="rounded border border-border px-1.5 py-0.5 text-[11px] hover:bg-accent"
                          onClick={() => setPendingPdfs((prev) => prev.map(
                            (x, j) => (j === i
                              ? { ...x, role: x.role === 'main' ? 'si' : 'main' }
                              : x)))}
                          title="切换角色（主文 / 补充信息 SI）"
                        >
                          {p.role === 'main' ? '主文' : 'SI'}
                        </button>
                        <button
                          type="button"
                          className="rounded p-0.5 text-muted-foreground hover:text-destructive"
                          onClick={() => setPendingPdfs(
                            (prev) => prev.filter((_, j) => j !== i))}
                          title="移除"
                        >
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    ))}
                  </div>
                )}

                <Textarea
                  value={parseText}
                  onChange={(e) => setParseText(e.target.value)}
                  rows={5}
                  placeholder="粘贴文献全文（或上传 PDF）；补充信息（SI）可与主文一起上传——解析 LLM 未配置时降级为 SMILES 正则扫描"
                />

                <DialogFooter className="flex-wrap gap-2">
                  <Button variant="outline"
                          onClick={() => pdfRef.current?.click()}
                          disabled={parseBusy
                            || pendingPdfs.length >= ATTACHMENT_MAX_PER_PAPER}>
                    <FileUp className="mr-1.5 h-4 w-4" />
                    选择文件（可多份）
                  </Button>
                  {attachments.length > 0 && (
                    <Button variant="outline"
                            onClick={() => void runParse({ useStored: true })}
                            disabled={parseBusy}>
                      <RefreshCw className="mr-1.5 h-4 w-4" />
                      用已存 {attachments.length} 个附件解析
                    </Button>
                  )}
                  <Button
                    onClick={() => void runParse()}
                    disabled={parseBusy || (!parseText.trim()
                      && pendingPdfs.length === 0 && attachments.length === 0)}>
                    {parseBusy
                      ? <><Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                          解析中… {parseElapsed}s</>
                      : '开始解析'}
                  </Button>
                </DialogFooter>

                {parseBusy && (
                  <div className="rounded-lg border border-gold/40 bg-gold-muted/30 px-2.5 py-2 text-[11px] text-muted-foreground">
                    正在逐段调用解析 LLM（含推理模型的思考 token，单段约 30–60 秒）：
                    主文与每份 SI 分别解析后合并去重。已用时 {parseElapsed} 秒，
                    长文献（100+ 页含 SI）可能需要数分钟，请勿关闭窗口。
                  </div>
                )}

                {/* 已存附件管理（主文 / SI 角色可切换、可删除） */}
                {attachments.length > 0 && (
                  <div className="space-y-1 rounded-lg border border-border p-2">
                    <p className="text-xs font-medium text-muted-foreground">
                      已存附件（{attachments.length}/{ATTACHMENT_MAX_PER_PAPER}）
                    </p>
                    {attachments.map((a) => (
                      <div key={a.file_id} className="flex items-center gap-2 text-xs">
                        <Badge variant={a.role === 'main' ? 'default' : 'outline'}
                               className="shrink-0 text-[10px]">
                          {a.role === 'main' ? '主文' : 'SI'}
                        </Badge>
                        <span className="min-w-0 flex-1 truncate" title={a.filename}>
                          {a.filename}
                          <span className="ml-1 text-muted-foreground/70">
                            {a.pages} 页 · {(a.size / 1024 / 1024).toFixed(1)}MB
                            {a.chars === 0 && ' · 无文本层'}
                          </span>
                        </span>
                        <button
                          type="button"
                          className="rounded border border-border px-1.5 py-0.5 text-[11px] hover:bg-accent"
                          onClick={() => void switchAttachmentRole(
                            a.file_id, a.role === 'main' ? 'si' : 'main')}
                          title="切换主文 / SI"
                        >
                          {a.role === 'main' ? '设为SI' : '设为主文'}
                        </button>
                        <a className="rounded border border-border px-1.5 py-0.5 text-[11px] hover:bg-accent"
                           href={attachmentUrl(a.file_id)} target="_blank"
                           rel="noreferrer">
                          下载
                        </a>
                        <button
                          type="button"
                          className="rounded p-0.5 text-muted-foreground hover:text-destructive"
                          onClick={() => void removeAttachment(a.file_id)}
                          title="删除附件"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <>
                {/* 单一滚动区：说明 + 来源 + 元数据 + 条目；底部按钮在其外，始终可见可点 */}
                <div className="min-h-0 flex-1 space-y-3 overflow-y-auto overflow-x-hidden pr-1">
                <p className="break-words text-xs text-muted-foreground">
                  {preview.llm_used ? 'LLM 结构化提取' : 'SMILES 正则扫描（降级）'}：
                  {preview.note}
                  {preview.segments && preview.segments.total > 1 && (
                    <span className="ml-1 text-muted-foreground/70">
                      （{preview.chars ?? 0} 字符
                      {preview.pages ? ` / ${preview.pages} 页` : ''}
                      ，分 {preview.segments.total} 段解析）
                    </span>
                  )}
                  （勾选要入库的条目，按组归类）
                </p>

                {/* v1.9.3：本次解析来源（主文/SI 分别解析后合并） */}
                {preview.sources && preview.sources.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 text-[11px]">
                    {preview.sources.map((s, i) => (
                      <span key={`${s.filename}-${i}`}
                            className="inline-flex items-center gap-1 rounded border border-border bg-muted/40 px-1.5 py-0.5">
                        <Badge variant={s.role === 'main' ? 'default' : 'outline'}
                               className="text-[10px]">
                          {s.role === 'main' ? '主文' : 'SI'}
                        </Badge>
                        <span className="max-w-52 truncate" title={s.filename}>
                          {s.filename}
                        </span>
                        <span className="text-muted-foreground/70">
                          {s.pages} 页 / {s.chars} 字符
                        </span>
                      </span>
                    ))}
                  </div>
                )}
                {preview.scanned && preview.scanned.length > 0 && (
                  <p className="text-[11px] text-destructive">
                    已跳过（无文本层，疑似扫描件）：{preview.scanned.join('、')}
                  </p>
                )}

                {/* v1.9.3：文献级元数据（可回填文献库，只补空字段不覆盖） */}
                {preview.paper_meta && Object.keys(preview.paper_meta).length > 0 && (
                  <div className="rounded-lg border border-gold/40 bg-gold-muted/30 p-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-xs font-medium text-gold-foreground">
                        文献信息（LLM 提取，可回填）
                      </p>
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-7 px-2 text-xs"
                        disabled={parseBusy}
                        onClick={() => void backfillMeta()}
                      >
                        <FileCheck2 className="mr-1 h-3 w-3" />
                        回填到文献库
                      </Button>
                    </div>
                    <dl className="mt-1.5 space-y-0.5 text-xs text-muted-foreground">
                      {preview.paper_meta.title && (
                        <div className="flex gap-1.5">
                          <dt className="shrink-0">标题：</dt>
                          <dd className="min-w-0 flex-1 text-foreground">
                            {preview.paper_meta.title}
                          </dd>
                        </div>
                      )}
                      {preview.paper_meta.authors?.length ? (
                        <div className="flex gap-1.5">
                          <dt className="shrink-0">作者：</dt>
                          <dd className="truncate">{preview.paper_meta.authors.join('，')}</dd>
                        </div>
                      ) : null}
                      {(preview.paper_meta.journal || preview.paper_meta.year) && (
                        <div className="flex gap-1.5">
                          <dt className="shrink-0">期刊：</dt>
                          <dd>
                            {[preview.paper_meta.journal, preview.paper_meta.year]
                              .filter(Boolean).join(' · ')}
                          </dd>
                        </div>
                      )}
                      {preview.paper_meta.doi && (
                        <div className="flex gap-1.5">
                          <dt className="shrink-0">DOI：</dt>
                          <dd className="truncate">{preview.paper_meta.doi}</dd>
                        </div>
                      )}
                    </dl>
                    <p className="mt-1 text-[11px] text-muted-foreground/70">
                      只补文献库中的空字段；已有值（Crossref 入库的标题/作者等）不会被覆盖。
                    </p>
                  </div>
                )}
                <div className="space-y-2">
                  {Object.entries(
                    preview.entries.reduce<Record<string, (Partial<Entry> & { idx: number })[]>>(
                      (acc, e, i) => {
                        const g = String(e.group_id ?? '未分组');
                        (acc[g] ??= []).push({ ...e, idx: i });
                        return acc;
                      }, {})).map(([gid, rows]) => (
                    <div key={gid} className="min-w-0 overflow-hidden rounded-lg border border-border">
                      <p className="truncate border-b border-border bg-muted/40 px-2 py-1 text-xs font-medium"
                         title={`组 ${gid}：${rows[0]?.experiment ?? ''}`}>
                        组 {gid}：{rows[0]?.experiment}
                      </p>
                      {rows.map((e) => (
                        <label key={e.idx}
                               className={cn(
                                 'flex min-w-0 cursor-pointer items-start gap-2 px-2 py-1.5 text-xs hover:bg-muted/40',
                                 e.valid === false && 'cursor-not-allowed opacity-60',
                               )}>
                          <input
                            type="checkbox"
                            className="mt-0.5"
                            disabled={e.valid === false}
                            checked={e.valid !== false && checked[e.idx] !== false}
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
                            <span className="ml-1 break-words text-muted-foreground">
                              {e.metrics?.map((m) => `${m.name}=${m.value}${m.unit ?? ''}`).join('，')}
                            </span>
                            {e.source_file && (
                              <span className="ml-1 inline-block max-w-full truncate rounded border border-border px-1 align-bottom text-[10px] text-muted-foreground">
                                {e.source_file}
                              </span>
                            )}
                            {e.valid === false && (
                              <span className="ml-1 break-words text-[11px] text-destructive">
                                不可入库：{e.invalid_reason}
                              </span>
                            )}
                            {/* 依据：两行截断 + 长 URL/SMILES 强制换行，避免溢出弹窗边框 */}
                            <span className="mt-0.5 line-clamp-2 break-words text-muted-foreground/70"
                                  title={e.evidence}>
                              依据：{e.evidence}
                            </span>
                          </span>
                        </label>
                      ))}
                    </div>
                  ))}
                </div>
                </div>
                <DialogFooter className="shrink-0 items-center gap-2 border-t border-border pt-3 sm:justify-between">
                  <span className="text-xs text-muted-foreground">
                    已勾选 {preview.entries.filter(
                      (e, i) => e.valid !== false && checked[i] !== false).length} 条
                    {preview.invalid_count
                      ? `（另有 ${preview.invalid_count} 条字段不完整已排除）`
                      : ''}
                  </span>
                  <span className="flex gap-2">
                    <Button variant="outline"
                            onClick={() => { setPreview(null); setParseText(''); }}>
                      重新解析
                    </Button>
                    <Button onClick={() => void submitParse()} disabled={parseBusy}>
                      {parseBusy ? '入库中…' : '勾选条目入库（同步知识图谱）'}
                    </Button>
                  </span>
                </DialogFooter>
              </>
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
              <Button className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
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
