/**
 * 文献库三栏浏览器（v1.9.7 P2，借鉴 Zotero 的信息架构）
 *
 * 左栏：集合树（默认 5 个 + 自定义 + 未分类，带计数）+ 标签云（带计数）
 * 中栏：**服务端检索**（q / sort / direction / collection / tag）+ 分页加载的文献列表
 * 右栏：选中文献的详情 —— 元数据、所属集合（可增删）、标签（可增删）、笔记（增删改）
 *
 * 设计原则（与方案一致）：
 * - 一切写操作都走新 API（`/api/library/*`），不动既有知识库卡片的流程；
 * - 顶部提示 DB 是「镜像 + 组织层」，可一键重建（不影响原文件）；
 * - 旧的「科研知识库」卡片保留在下方（补解析 / 条目 / 文献图片仍在那里操作）。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  BookOpen, ChevronRight, FolderPlus, Loader2, Network, NotebookPen, Plus,
  RefreshCw, Search, Sparkles, Tag as TagIcon, Trash2, X,
} from 'lucide-react';
import { toast } from 'sonner';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';

const BASE = '/api/library';

interface PaperRow {
  paper_id: string;
  title: string;
  journal: string;
  year: number | null;
  doi: string;
  source: string;
  added_at: string;
  /** v1.9.8 R1-d：解析状态（供列表徽标；也是 R2「补缺解析」的判定依据） */
  parse_status?: 'parsed' | 'partial' | 'none';
  missing_parts?: string[];
  entries_count?: number;
  figures_count?: number;
  has_abstract?: boolean;
  /** v1.9.8 C1：混合检索命中来源（vector / graph）与融合得分 */
  sources?: string[];
  score?: number;
}
interface Collection {
  collection_id: string;
  name: string;
  parent_id: string | null;
  count: number;
}
interface TagRow { name: string; count: number; type: string }
interface Note {
  note_id: string; title: string; content_md: string;
  source: string; created_at: string; updated_at: string;
}
interface PaperDetail extends PaperRow {
  abstract: string | null;
  authors: string[];
  url: string;
  collections: string[];
  tags: string[];
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
      ...init,
    });
  } catch {
    throw new Error('无法连接后端服务');
  }
  if (!res.ok) {
    let msg = `请求失败（${res.status}）`;
    try {
      const data = await res.json();
      if (typeof data?.detail === 'string') msg = data.detail;
    } catch { /* 非 JSON */ }
    throw new Error(msg);
  }
  return (await res.json()) as T;
}

export function LibraryExplorer({
  onSelectPaper,
  compact,
}: {
  /** v1.9.8 R1 合并：选中文献时回调（由「科研知识库」驱动下方文献卡片） */
  onSelectPaper?: (paperId: string) => void;
  /** 精简模式：作为导航壳时隐藏右侧详情列（详情交给下方文献卡片） */
  compact?: boolean;
} = {}) {
  // 左栏
  const [collections, setCollections] = useState<Collection[]>([]);
  const [tags, setTags] = useState<TagRow[]>([]);
  const [activeCollection, setActiveCollection] = useState<string>('');
  const [activeTag, setActiveTag] = useState<string>('');
  const [newCollection, setNewCollection] = useState('');
  const [stats, setStats] = useState<Record<string, number> | null>(null);
  const [verifyOk, setVerifyOk] = useState<boolean | null>(null);

  // 中栏
  const [q, setQ] = useState('');
  const [sort, setSort] = useState('paper_id');
  /** v1.9.8 C1：检索模式 —— full=全文(FTS) / vector=向量 / graph=图 / hybrid=RRF 融合 */
  const [searchMode, setSearchMode] = useState<'full' | 'vector' | 'graph' | 'hybrid'>('full');
  const [degraded, setDegraded] = useState(false);
  const [routeNotes, setRouteNotes] = useState<string[]>([]);
  /** 混合检索进行中（首次会加载本地向量模型，可能数十秒） */
  const [hybridBusy, setHybridBusy] = useState(false);
  /** v1.9.8 C3/C5：补全与导出 */
  const [enriching, setEnriching] = useState(false);
  /** G0：构建图谱进行中 */
  const [graphBusy, setGraphBusy] = useState(false);

  /** C3：批量补全缺失元数据（默认只处理缺年份的文献；联网但免费） */
  const enrichMissing = async () => {
    let missing = 0;
    try {
      const st = await (await fetch('/api/library/enrich/status')).json();
      missing = Number(st?.missing_year || 0);
    } catch { /* 状态拿不到也允许继续 */ }
    const ok = window.confirm(
      `用 OpenAlex / Semantic Scholar 补全元数据？\n\n`
      + `将处理**缺少年份**的文献（当前 ${missing} 篇，每次最多 20 篇），`
      + `按 DOI 查询（免费、无需 key、约 1 秒/篇）。\n`
      + `只补空字段，不覆盖已有内容。继续？`);
    if (!ok) return;
    setEnriching(true);
    try {
      const r = await fetch('/api/library/enrich?limit=20', { method: 'POST' });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || `补全失败（${r.status}）`);
      toast.success(`补全完成：处理 ${d.targets} 篇（成功 ${d.enriched}、跳过 ${d.skipped}、`
                    + `错误 ${d.errors}）`);
      await Promise.all([loadStatus(), search(0)]);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '补全失败');
    } finally {
      setEnriching(false);
    }
  };

  /** G0：构建图谱（同步 cites / similar_to / 实体），完成后通知分析面板刷新 */
  const buildGraph = async () => {
    let papers = 0;
    try {
      const st = await (await fetch('/api/library/enrich/status')).json();
      papers = Number(st?.with_doi || 0);
    } catch { /* 拿不到状态也允许继续 */ }
    // 注意：Electron **不支持 window.prompt**（调用即返回空 → 表现为"点了没反应"），
    // 因此这里只用 confirm（Electron 支持），篇数固定 50，可在提示里说明。
    const limit = 50;
    const ok = window.confirm(
      `构建文献图谱（同步文献间关系）\n\n`
      + `将处理最近入库的 50 篇（你的库中约 ${papers} 篇有 DOI 可直接查引用）：\n`
      + `· cites：按 DOI 调 Crossref 取参考文献（每篇 1 次网络请求）\n`
      + `· similar_to：用本地向量算近邻（首次需加载模型，较慢）\n`
      + `· 实体：从已解析条目抽技术/指标/属性\n\n`
      + `建议先点「补全元数据」把年份补齐。继续？`);
    if (!ok) return;
    setGraphBusy(true);
    try {
      const r = await fetch(`/api/library/graph/sync?kinds=citations,similar,entities`
                            + `&limit=${Math.max(1, Math.min(500, limit))}`, { method: 'POST' });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || `构建失败（${r.status}）`);
      const st = d.stats || {};
      toast.success(`图谱已更新：节点 ${st.nodes ?? '-'} · 边 ${st.edges ?? '-'}`
                    + `（${Object.entries(st.by_predicate || {}).map(([k, v]) => `${k}:${v}`).join(' ')}）`);
      window.dispatchEvent(new CustomEvent('cof:graph-updated'));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '构建图谱失败');
    } finally {
      setGraphBusy(false);
    }
  };

  /** C5：导出关联图谱（jsoncanvas 下载 / graphml 下载 / html 新窗口打开） */
  const exportGraph = (format: string) => {
    const url = `/api/library/graph/export?format=${format}&max_nodes=300`;
    if (format === 'html') {
      window.open(url, '_blank');
      toast.info('已在新窗口打开离线图谱（可拖拽/缩放）');
      return;
    }
    const a = document.createElement('a');
    a.href = url;
    a.download = format === 'graphml' ? 'cof-graph.graphml' : 'cof-graph.canvas';
    document.body.appendChild(a);
    a.click();
    a.remove();
    toast.success(`已导出 ${format === 'graphml' ? 'GraphML' : 'Obsidian 画布'}`);
  };
  const [direction, setDirection] = useState('desc');
  const [papers, setPapers] = useState<PaperRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<string>('');

  // 右栏
  const [detail, setDetail] = useState<PaperDetail | null>(null);
  const [notes, setNotes] = useState<Note[]>([]);
  const [noteDraft, setNoteDraft] = useState('');
  const [tagDraft, setTagDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | null>(null);

  const loadSidebar = useCallback(async () => {
    try {
      const [c, t] = await Promise.all([
        req<{ collections: Collection[] }>('/collections'),
        req<{ tags: TagRow[] }>('/tags'),
      ]);
      setCollections(c.collections ?? []);
      setTags(t.tags ?? []);
    } catch { /* 首次会触发迁移，失败则静默 */ }
  }, []);

  const loadStatus = useCallback(async () => {
    try {
      const s = await req<{ stats: Record<string, number>; verify: { ok: boolean } }>('/status');
      setStats(s.stats);
      setVerifyOk(Boolean(s.verify?.ok));
    } catch { /* ignore */ }
  }, []);

  const search = useCallback(async (offset = 0) => {
    setLoading(true);
    try {
      // C1：非全文模式走混合检索（RRF 融合 + 逐条来源标注）
      if (searchMode !== 'full' && offset === 0) {
        // 首次向量检索需在服务端加载本地嵌入模型（实测 30–60s）→ 必须清空列表并提示，
        // 否则会长时间显示**上一次的结果**，让人误以为徽标/检索没生效（已实测踩坑）
        setPapers([]);
        setTotal(0);
        setHybridBusy(true);
        const r = await fetch(`/api/library/search/hybrid?q=${encodeURIComponent(q)}`
                              + `&mode=${searchMode}&top_k=100`);
        const d = r.ok ? await r.json() : { results: [] };
        setDegraded(Boolean(d.degraded));
        setRouteNotes(d.notes ?? []);
        const rows = (d.results ?? []).map((x: {
          paper_id: string; title: string; journal: string; year: number | null;
          doi: string; sources?: string[]; score?: number;
        }) => ({
          paper_id: x.paper_id, title: x.title, journal: x.journal, year: x.year,
          doi: x.doi, source: '', added_at: '',
          sources: x.sources ?? [], score: x.score,
        }));
        setPapers(rows);
        setTotal(rows.length);
        setHybridBusy(false);
        return;
      }
      // 竞态防护：非全文模式下，全文检索的结果**不得覆盖**混合检索结果
      // （模式切换时防抖会先后触发两次请求，慢的那次会把 sources 冲掉 →
      //  表现为"行在、来源徽标为空"，本缺陷已实测确认）
      if (searchMode !== 'full') return;
      const params = new URLSearchParams({
        q, sort, direction, limit: '50', offset: String(offset),
      });
      if (activeCollection) params.set('collection_id', activeCollection);
      if (activeTag) params.set('tag', activeTag);
      const data = await req<{ papers: PaperRow[]; total: number }>(`/papers?${params}`);
      setPapers((prev) => (offset === 0 ? data.papers : [...prev, ...data.papers]));
      setTotal(data.total);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '检索失败');
    } finally {
      setLoading(false);
    }
  } , [q, sort, direction, activeCollection, activeTag, searchMode]);

  useEffect(() => { void loadSidebar(); void loadStatus(); }, [loadSidebar, loadStatus]);

  // 检索防抖（搜索框输入 300ms 后触发）
  useEffect(() => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => { void search(0); }, 300);
    return () => { if (timer.current) window.clearTimeout(timer.current); };
  }, [search]);

  const openPaper = async (paperId: string) => {
    setSelected(paperId);
    onSelectPaper?.(paperId);        // R1：通知外层（科研知识库）切换详情
    if (compact) return;             // 精简模式不加载右侧详情
    setDetail(null);
    try {
      const [d, n] = await Promise.all([
        req<PaperDetail>(`/papers/${encodeURIComponent(paperId)}`),
        req<{ notes: Note[] }>(`/papers/${encodeURIComponent(paperId)}/notes`),
      ]);
      setDetail(d);
      setNotes(n.notes ?? []);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '加载详情失败');
    }
  };

  const toggleCollection = async (collectionId: string, has: boolean) => {
    if (!detail) return;
    try {
      await req(`/papers/${encodeURIComponent(detail.paper_id)}/collections/${collectionId}`,
        { method: has ? 'DELETE' : 'POST' });
      const d = await req<PaperDetail>(`/papers/${encodeURIComponent(detail.paper_id)}`);
      setDetail(d);
      await loadSidebar();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '操作失败');
    }
  };

  const addTag = async () => {
    if (!detail || !tagDraft.trim()) return;
    try {
      await req(`/papers/${encodeURIComponent(detail.paper_id)}/tags`,
        { method: 'POST', body: JSON.stringify({ tag: tagDraft.trim() }) });
      setTagDraft('');
      setDetail(await req<PaperDetail>(`/papers/${encodeURIComponent(detail.paper_id)}`));
      await loadSidebar();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '打标签失败');
    }
  };

  const removeTag = async (tag: string) => {
    if (!detail) return;
    try {
      await req(`/papers/${encodeURIComponent(detail.paper_id)}/tags/${encodeURIComponent(tag)}`,
        { method: 'DELETE' });
      setDetail(await req<PaperDetail>(`/papers/${encodeURIComponent(detail.paper_id)}`));
      await loadSidebar();
    } catch { /* 已提示 */ }
  };

  const createCollection = async () => {
    const name = newCollection.trim();
    if (!name) return;
    try {
      await req('/collections', { method: 'POST', body: JSON.stringify({ name }) });
      setNewCollection('');
      await loadSidebar();
      toast.success(`已创建集合「${name}」`);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '创建失败');
    }
  };

  const addNote = async () => {
    if (!detail || !noteDraft.trim()) return;
    setBusy(true);
    try {
      await req(`/papers/${encodeURIComponent(detail.paper_id)}/notes`, {
        method: 'POST',
        body: JSON.stringify({ content_md: noteDraft.trim(), source: 'user' }),
      });
      setNoteDraft('');
      const n = await req<{ notes: Note[] }>(`/papers/${encodeURIComponent(detail.paper_id)}/notes`);
      setNotes(n.notes ?? []);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '保存笔记失败');
    } finally {
      setBusy(false);
    }
  };

  const deleteNote = async (noteId: string) => {
    try {
      await req(`/notes/${noteId}`, { method: 'DELETE' });
      setNotes((prev) => prev.filter((n) => n.note_id !== noteId));
    } catch { /* 已提示 */ }
  };

  const rebuild = async () => {
    if (!window.confirm('用既有文件重建文献库索引？原文件不受影响（仍会先做只读备份）。')) return;
    setBusy(true);
    try {
      await req('/rebuild', { method: 'POST' });
      await Promise.all([loadSidebar(), loadStatus()]);
      await search(0);
      toast.success('文献库索引已重建');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '重建失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-base">文献库（三栏浏览）</CardTitle>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            {stats && (
              <span>
                {stats.papers} 篇 · {stats.figures} 图 · {stats.entries} 条目 · {stats.notes} 笔记
                {verifyOk === false && <span className="ml-1 text-destructive">（索引校验不一致）</span>}
              </span>
            )}
            <Button size="sm" variant="outline" className="h-7 px-2 text-xs"
                    disabled={busy} onClick={() => void rebuild()}>
              <RefreshCw className={cn('mr-1 h-3 w-3', busy && 'animate-spin')} />重建索引
            </Button>
            {/* G0：构建图谱（让引用图分析与图查询有数据） */}
            <Button size="sm" variant="outline" className="h-7 px-2 text-xs"
                    disabled={graphBusy}
                    title="同步文献间关系：cites（按 DOI 调 Crossref）、similar_to（向量近邻）、实体（技术/指标/属性）"
                    onClick={() => void buildGraph()}>
              <Network className={cn('mr-1 h-3 w-3', graphBusy && 'animate-pulse')} />
              {graphBusy ? '建图中…' : '构建图谱'}
            </Button>
            {/* v1.9.8 C3：OpenAlex / Semantic Scholar 补全（免费无 key） */}
            <Button size="sm" variant="outline" className="h-7 px-2 text-xs"
                    disabled={enriching}
                    title="按 DOI 从 OpenAlex / Semantic Scholar 补全缺失的年份、期刊与被引量（只补空、不覆盖）"
                    onClick={() => void enrichMissing()}>
              <Sparkles className={cn('mr-1 h-3 w-3', enriching && 'animate-pulse')} />
              {enriching ? '补全中…' : '补全元数据'}
            </Button>
            {/* v1.9.8 C5：图谱导出（JSON Canvas / GraphML / 离线 HTML） */}
            <select className="h-7 rounded-md border border-input bg-background px-1 text-[11px]"
                    value=""
                    title="导出关联图谱：Obsidian 画布 / GraphML（Gephi、yEd）/ 离线可交互页面"
                    onChange={(e) => { if (e.target.value) exportGraph(e.target.value); }}>
              <option value="">导出图谱…</option>
              <option value="jsoncanvas">Obsidian 画布（.canvas）</option>
              <option value="graphml">GraphML（Gephi/yEd）</option>
              <option value="html">离线可交互页面</option>
            </select>
          </div>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <div className={cn('grid grid-cols-1 overflow-hidden border-t border-border',
                           'h-[calc(100vh-340px)] min-h-[420px]',
                           compact ? 'lg:grid-cols-[220px_minmax(0,1fr)]'
                                   : 'lg:grid-cols-[220px_minmax(0,1fr)_360px]')}>
          {/* 左：集合 + 标签 */}
          <div className="hidden border-r border-border p-3 lg:block">
            <p className="mb-2 flex items-center gap-1 text-xs font-medium text-muted-foreground">
              <BookOpen className="h-3.5 w-3.5" />集合
            </p>
            <div className="space-y-0.5">
              {collections.map((c) => (
                <button
                  key={c.collection_id || 'none'}
                  type="button"
                  onClick={() => { setActiveCollection(c.collection_id); setActiveTag(''); }}
                  className={cn(
                    'flex w-full items-center justify-between rounded-md px-2 py-1 text-left text-sm transition-colors',
                    activeCollection === c.collection_id
                      ? 'bg-primary/10 font-medium text-primary'
                      : 'hover:bg-accent',
                  )}
                >
                  <span className="truncate">{c.name}</span>
                  <span className="ml-1 shrink-0 text-[11px] text-muted-foreground">{c.count}</span>
                </button>
              ))}
            </div>
            <div className="mt-2 flex gap-1">
              <Input value={newCollection} onChange={(e) => setNewCollection(e.target.value)}
                     onKeyDown={(e) => { if (e.key === 'Enter') void createCollection(); }}
                     placeholder="新建集合" className="h-7 text-xs" />
              <Button size="sm" variant="outline" className="h-7 px-2"
                      onClick={() => void createCollection()}>
                <FolderPlus className="h-3.5 w-3.5" />
              </Button>
            </div>

            <p className="mb-2 mt-4 flex items-center gap-1 text-xs font-medium text-muted-foreground">
              <TagIcon className="h-3.5 w-3.5" />标签
            </p>
            <div className="flex flex-wrap gap-1">
              {tags.length === 0 && (
                <span className="text-xs text-muted-foreground">暂无标签（在右侧给文献打标签）</span>
              )}
              {tags.map((t) => (
                <button key={t.name} type="button"
                        onClick={() => { setActiveTag(activeTag === t.name ? '' : t.name); setActiveCollection(''); }}
                        className={cn(
                          'rounded-full border px-2 py-0.5 text-[11px] transition-colors',
                          activeTag === t.name
                            ? 'border-primary bg-primary/10 text-primary'
                            : 'border-border hover:bg-accent',
                        )}>
                  {t.name} · {t.count}
                </button>
              ))}
            </div>
          </div>

          {/* 中：检索 + 列表 */}
          <div className="flex min-w-0 flex-col">
            <div className="flex flex-wrap items-center gap-2 border-b border-border p-2">
              <div className="relative min-w-[180px] flex-1">
                <Search className="pointer-events-none absolute left-2 top-2 h-3.5 w-3.5 text-muted-foreground" />
                <Input value={q} onChange={(e) => setQ(e.target.value)}
                       placeholder="全文检索（标题 / 摘要 / 作者 / 标签）"
                       className="h-8 pl-7 text-sm" />
                {q && (
                  <button type="button" title="清空" onClick={() => setQ('')}
                          className="absolute right-2 top-2 text-muted-foreground hover:text-foreground">
                    <X className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
              <select value={sort} onChange={(e) => setSort(e.target.value)}
                      className="h-8 rounded-md border border-input bg-background px-2 text-xs">
                <option value="paper_id">按编号</option>
                <option value="year">按年份</option>
                <option value="added_at">按入库时间</option>
                <option value="title">按标题</option>
              </select>
              {/* v1.9.8 C1：三态检索开关（向量 / 图 / RRF 融合） */}
              <select value={searchMode}
                      onChange={(e) => setSearchMode(e.target.value as typeof searchMode)}
                      title="检索模式：全文为 FTS 关键词；向量/图/混合走服务端融合检索（命中会标注来源）"
                      className="h-8 rounded-md border border-input bg-background px-2 text-xs">
                <option value="full">全文检索</option>
                <option value="vector">向量召回</option>
                <option value="graph">图谱召回</option>
                <option value="hybrid">混合（RRF）</option>
              </select>
              <Button size="sm" variant="outline" className="h-8 px-2 text-xs"
                      onClick={() => setDirection(direction === 'desc' ? 'asc' : 'desc')}>
                {direction === 'desc' ? '倒序' : '正序'}
              </Button>
            </div>

            <div className="flex items-center justify-between px-3 py-1.5 text-[11px] text-muted-foreground">
              <span>
                共 {total} 篇
                {activeCollection && ` · 集合：${collections.find((c) => c.collection_id === activeCollection)?.name ?? ''}`}
                {activeTag && ` · 标签：${activeTag}`}
                {searchMode !== 'full' && ` · 模式：${
                  searchMode === 'hybrid' ? '混合(RRF)' : searchMode === 'vector' ? '向量' : '图谱'}`}
              {/* C1：检索进行中提示（首次向量检索要加载本地模型，实测 30–60s） */}
              {hybridBusy && (
                <span className="ml-2 text-[10px] text-amber-600">
                  正在做{searchMode === 'hybrid' ? '混合' : searchMode === 'vector' ? '向量' : '图谱'}
                  检索…（首次会加载本地向量模型，可能 30–60 秒）
                </span>
              )}
              </span>
              {/* C1：降级提示（如向量模型不可用 → 自动退化为图谱路） */}
              {degraded && (
                <span className="text-amber-600" title={routeNotes.join('；')}>
                  已降级（仅图谱路）
                </span>
              )}
              {loading && <Loader2 className="h-3 w-3 animate-spin" />}
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto">
              {papers.map((p) => (
                <button
                  key={p.paper_id}
                  type="button"
                  onClick={() => void openPaper(p.paper_id)}
                  className={cn(
                    'flex w-full items-start gap-2 border-b border-border/60 px-3 py-2 text-left transition-colors',
                    selected === p.paper_id ? 'bg-primary/5' : 'hover:bg-accent/50',
                  )}
                >
                  <ChevronRight className={cn('mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground',
                                              selected === p.paper_id && 'text-primary')} />
                  <span className="min-w-0 flex-1">
                    <span className="line-clamp-2 text-sm text-foreground">
                      {p.title || '（无标题）'}
                    </span>
                    <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">
                      #{p.paper_id}
                      {p.journal ? ` · ${p.journal}` : ''}
                      {p.year ? ` · ${p.year}` : ''}
                      {p.doi ? ` · ${p.doi}` : ''}
                    </span>
                    {/* C1：混合检索的来源标注（哪几路命中）
                        注意：非全文模式**无条件渲染**（无来源时显示占位），
                        否则一旦 sources 为空就完全看不见，难以判断是"没命中"还是"没渲染" */}
                    {searchMode !== 'full' && (
                      <span className="mt-1 flex flex-wrap items-center gap-1">
                        {(p.sources && p.sources.length > 0) ? (
                          <>
                            {p.sources.map((s) => (
                              <span key={s}
                                    className={cn('rounded-full border px-1.5 py-0.5 text-[10px]',
                                      s === 'graph'
                                        ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-600'
                                        : 'border-sky-500/40 bg-sky-500/10 text-sky-600')}>
                                {s === 'graph' ? '图谱' : '向量'}
                              </span>
                            ))}
                            {typeof p.score === 'number' && (
                              <span className="text-[10px] text-muted-foreground">
                                RRF {p.score.toFixed(4)}
                              </span>
                            )}
                          </>
                        ) : (
                          <span className="text-[10px] text-muted-foreground">来源：—</span>
                        )}
                      </span>
                    )}
                    {/* R1-d：解析状态徽标（未解析 / 部分 / 已解析），缺失项以提示列出 */}
                    {p.parse_status && (
                      <span
                        title={p.missing_parts?.length
                          ? `缺少：${p.missing_parts.map((m) => (
                              m === 'abstract' ? '摘要' : m === 'entries' ? '结构化条目' : '文献图片'
                            )).join('、')}`
                          : '摘要、结构化条目、文献图片均已就绪'}
                        className={cn(
                          'mt-1 inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px]',
                          p.parse_status === 'parsed'
                            ? 'border-primary/40 bg-primary/10 text-primary'
                            : p.parse_status === 'partial'
                              ? 'border-gold/50 bg-gold-muted/40 text-gold-foreground'
                              : 'border-border bg-muted/40 text-muted-foreground',
                        )}
                      >
                        {p.parse_status === 'parsed' ? '已解析'
                          : p.parse_status === 'partial' ? '部分解析' : '未解析'}
                        {p.parse_status !== 'parsed' && p.missing_parts?.length
                          ? ` · 缺 ${p.missing_parts.length} 项` : ''}
                      </span>
                    )}
                  </span>
                </button>
              ))}
              {papers.length === 0 && !loading && (
                <p className="p-4 text-center text-sm text-muted-foreground">没有匹配的文献</p>
              )}
              {papers.length < total && (
                <div className="p-2 text-center">
                  <Button size="sm" variant="outline" disabled={loading}
                          onClick={() => void search(papers.length)}>
                    加载更多（{papers.length}/{total}）
                  </Button>
                </div>
              )}
            </div>
          </div>

          {/* 右：详情（精简模式下隐藏，详情由外层文献卡片承担） */}
          {!compact && (
          <div className="min-w-0 border-l border-border p-3">
            {!detail ? (
              <p className="mt-8 text-center text-sm text-muted-foreground">
                选择左侧文献查看详情
                <br />
                <span className="text-xs">
                  （补解析、结构化条目、文献图片仍在下方「科研知识库」卡片操作）
                </span>
              </p>
            ) : (
              <div className="space-y-3">
                <div>
                  <p className="text-sm font-medium leading-snug">{detail.title || '（无标题）'}</p>
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    #{detail.paper_id}
                    {detail.authors.length > 0 && ` · ${detail.authors.slice(0, 3).join('，')}`}
                    {detail.authors.length > 3 && ' 等'}
                  </p>
                  <p className="text-[11px] text-muted-foreground">
                    {[detail.journal, detail.year].filter(Boolean).join(' · ') || '期刊信息缺失'}
                    {detail.doi && ` · ${detail.doi}`}
                  </p>
                </div>

                {detail.abstract ? (
                  <p className="line-clamp-4 text-xs text-muted-foreground" title={detail.abstract}>
                    {detail.abstract}
                  </p>
                ) : (
                  <p className="text-xs text-muted-foreground">（暂无摘要：可在下方知识库卡片补解析）</p>
                )}

                <div>
                  <p className="mb-1 text-xs font-medium text-muted-foreground">所属集合</p>
                  <div className="flex flex-wrap gap-1">
                    {collections.filter((c) => c.collection_id).map((c) => {
                      const has = detail.collections.includes(c.collection_id);
                      return (
                        <button key={c.collection_id} type="button"
                                onClick={() => void toggleCollection(c.collection_id, has)}
                                className={cn(
                                  'rounded-full border px-2 py-0.5 text-[11px] transition-colors',
                                  has ? 'border-primary bg-primary/10 text-primary'
                                      : 'border-border hover:bg-accent',
                                )}>
                          {has ? '✓ ' : '+ '}{c.name}
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div>
                  <p className="mb-1 text-xs font-medium text-muted-foreground">标签</p>
                  <div className="mb-1 flex flex-wrap gap-1">
                    {detail.tags.length === 0 && (
                      <span className="text-[11px] text-muted-foreground">暂无</span>
                    )}
                    {detail.tags.map((t) => (
                      <Badge key={t} variant="outline" className="gap-1 text-[11px]">
                        {t}
                        <button type="button" title="移除标签"
                                onClick={() => void removeTag(t)}>
                          <X className="h-3 w-3" />
                        </button>
                      </Badge>
                    ))}
                  </div>
                  <div className="flex gap-1">
                    <Input value={tagDraft} onChange={(e) => setTagDraft(e.target.value)}
                           onKeyDown={(e) => { if (e.key === 'Enter') void addTag(); }}
                           placeholder="添加标签" className="h-7 text-xs" />
                    <Button size="sm" variant="outline" className="h-7 px-2"
                            onClick={() => void addTag()}>
                      <Plus className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>

                <div>
                  <p className="mb-1 flex items-center gap-1 text-xs font-medium text-muted-foreground">
                    <NotebookPen className="h-3.5 w-3.5" />笔记（{notes.length}）
                  </p>
                  <div className="space-y-1.5">
                    {notes.map((n) => (
                      <div key={n.note_id}
                           className="rounded-md border border-border/70 bg-muted/20 p-2">
                        <div className="flex items-start justify-between gap-1">
                          <p className="text-[11px] font-medium">
                            {n.title || '（无标题）'}
                            <span className="ml-1 font-normal text-muted-foreground">
                              {n.source === 'llm' ? 'LLM' : n.source === 'assistant' ? '助手' : '手动'}
                            </span>
                          </p>
                          <button type="button" title="删除笔记"
                                  onClick={() => void deleteNote(n.note_id)}
                                  className="text-muted-foreground hover:text-destructive">
                            <Trash2 className="h-3 w-3" />
                          </button>
                        </div>
                        <p className="mt-0.5 whitespace-pre-wrap break-words text-[11px] text-muted-foreground">
                          {n.content_md.slice(0, 200)}
                        </p>
                      </div>
                    ))}
                  </div>
                  <Textarea value={noteDraft} onChange={(e) => setNoteDraft(e.target.value)}
                            rows={3} placeholder="写一条笔记（Markdown）…"
                            className="mt-1.5 text-xs" />
                  <Button size="sm" className="mt-1 h-7 text-xs" disabled={busy}
                          onClick={() => void addNote()}>
                    保存笔记
                  </Button>
                </div>
              </div>
            )}
          </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
