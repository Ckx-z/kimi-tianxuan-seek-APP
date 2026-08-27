/**
 * 「文献录入」区块（我的页，放「导出实验记录」上方）
 * 三步交互流：
 * 1. 输入框粘 DOI 或文献标题 →「查询」（DOI 自动识别：10.xxxx/ 前缀或 doi.org 链接）
 * 2. 审核：标题查询先给候选列表（前 3）点选；DOI 直接进审核卡。
 *    草稿全字段可编辑（标题/作者/期刊/年份/DOI/摘要）——不正确的可修改后再入库；
 *    existing=true 时黄色提示「库中已有此文献（#paper_id）」并禁止确认。
 * 3.「确认入库」→ confirm（reviewed_by 固定 "user"）→ 成功面板含新 paper_id 与
 *   「不入训练集、暂不入图谱」说明；409 展示已存在 paper_id；
 *   502 中文提示「Crossref 暂时不可达，请稍后重试」。
 */
import { useState } from 'react';
import { BookPlus, Search, AlertTriangle, CheckCircle2, RotateCcw, Loader2, FileUp } from 'lucide-react';
import { toast } from 'sonner';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  confirmLiterature,
  extractLiteratureFromPdf,
  lookupLiteratureByDoi,
  lookupLiteratureByTitle,
  LiteratureApiError,
  type LiteratureConfirmResult,
  type LiteratureDraft,
} from './api';
import { openExternal } from '@/lib/external';

/** 输入是否按 DOI 处理（10.xxxx/ 前缀或 doi.org 链接） */
function looksLikeDoi(q: string): boolean {
  return /^(https?:\/\/(dx\.)?doi\.org\/)?10\.\d{4,9}\/\S+$/i.test(q.trim());
}

/** 审核表单状态（作者/摘要用多行文本编辑，提交时再结构化） */
interface DraftForm {
  title: string;
  authorsText: string; // 每行一位作者
  journal: string;
  yearText: string;
  doi: string;
  abstractText: string;
  existing: boolean;
  existingPaperId?: string;
  /** 草稿来源（crossref / pdf-llm），confirm 时透传给审计 */
  source: string;
  /** PDF 提取通道的原始文件名（pdf-llm 时存在） */
  pdfFilename?: string;
}

function draftToForm(d: LiteratureDraft): DraftForm {
  return {
    title: d.title ?? '',
    authorsText: (d.authors ?? []).join('\n'),
    journal: d.journal ?? '',
    yearText: typeof d.year === 'number' ? String(d.year) : '',
    doi: d.doi ?? '',
    abstractText: d.abstract ?? '',
    existing: d.existing === true,
    existingPaperId: d.existing_paper_id,
    source: d.source ?? 'crossref',
    pdfFilename: d.pdf_filename,
  };
}

/** 候选/审核卡共用的文献元信息一行 */
function DraftMetaLine({ d }: { d: LiteratureDraft }) {
  return (
    <span className="text-xs text-muted-foreground">
      {[d.journal, d.year, d.doi ? `DOI: ${d.doi}` : null].filter(Boolean).join(' · ') || '—'}
    </span>
  );
}

export function LiteratureIntakeSection() {
  /** 录入方式 Tab：doi / title / pdf */
  const [mode, setMode] = useState<'doi' | 'title' | 'pdf'>('doi');
  const [query, setQuery] = useState('');
  const [searching, setSearching] = useState(false);
  /** PDF 上传通道：选中的文件与提取状态 */
  const [pdfFile, setPdfFile] = useState<File | null>(null);
  const [extracting, setExtracting] = useState(false);
  /** 标题查询的候选列表（非空时先选候选再进审核） */
  const [candidates, setCandidates] = useState<LiteratureDraft[] | null>(null);
  /** 审核中的草稿表单（非空即进入第二步） */
  const [form, setForm] = useState<DraftForm | null>(null);
  const [confirming, setConfirming] = useState(false);
  /** 第三步：入库成功结果 */
  const [result, setResult] = useState<LiteratureConfirmResult | null>(null);
  /** 内联错误（lookup 404/502、confirm 409 等） */
  const [error, setError] = useState<{ message: string; existingPaperId?: string } | null>(null);

  /** 重置回第一步（保留/清空输入由调用方决定） */
  const reset = (clearQuery: boolean) => {
    if (clearQuery) {
      setQuery('');
      setPdfFile(null);
    }
    setCandidates(null);
    setForm(null);
    setResult(null);
    setError(null);
  };

  /** 第一步：查询（DOI → 直接草稿；标题 → 候选列表） */
  const handleLookup = async () => {
    const q = query.trim();
    if (!q) {
      toast.error(mode === 'doi' ? '请输入 DOI' : '请输入文献标题');
      return;
    }
    if (mode === 'doi' && !looksLikeDoi(q)) {
      toast.error('DOI 格式不正确（形如 10.xxxx/... 或 doi.org 链接）');
      return;
    }
    setSearching(true);
    setError(null);
    setResult(null);
    setCandidates(null);
    setForm(null);
    try {
      if (mode === 'doi') {
        const draft = await lookupLiteratureByDoi(q);
        setForm(draftToForm(draft));
      } else {
        const list = await lookupLiteratureByTitle(q);
        if (list.length === 0) {
          setError({ message: 'Crossref 未检索到相关候选，请换关键词或直接粘贴 DOI' });
        } else if (list.length === 1) {
          setForm(draftToForm(list[0]));
        } else {
          setCandidates(list);
        }
      }
    } catch (e) {
      if (e instanceof LiteratureApiError) {
        setError({ message: e.message, existingPaperId: e.existingPaperId });
      }
      /* 网络层错误已由 api 层 toast */
    } finally {
      setSearching(false);
    }
  };

  /** 第一步（PDF 通道）：上传 → LLM 提取 → 直接进审核卡 */
  const handleExtractPdf = async () => {
    if (!pdfFile) {
      toast.error('请先选择 PDF 文件');
      return;
    }
    if (!pdfFile.name.toLowerCase().endsWith('.pdf')) {
      toast.error('请选择 .pdf 文件');
      return;
    }
    if (pdfFile.size > 20 * 1024 * 1024) {
      setError({ message: 'PDF 超过 20MB 上限，请压缩后重试' });
      return;
    }
    setExtracting(true);
    setError(null);
    setResult(null);
    setCandidates(null);
    setForm(null);
    try {
      const draft = await extractLiteratureFromPdf(pdfFile);
      setForm(draftToForm(draft));
    } catch (e) {
      if (e instanceof LiteratureApiError) {
        setError({ message: e.message, existingPaperId: e.existingPaperId });
      }
      /* 网络层错误已由 api 层 toast */
    } finally {
      setExtracting(false);
    }
  };

  /** 第三步：确认入库（审核后可改；409 展示已存在 paper_id） */
  const handleConfirm = async () => {
    if (!form) return;
    if (!form.title.trim()) {
      toast.error('文献标题不能为空');
      return;
    }
    setConfirming(true);
    setError(null);
    try {
      const year = form.yearText.trim() ? Number.parseInt(form.yearText.trim(), 10) : null;
      const res = await confirmLiterature({
        title: form.title.trim(),
        authors: form.authorsText.split('\n').map((s) => s.trim()).filter(Boolean),
        journal: form.journal.trim(),
        year: Number.isFinite(year) ? year : null,
        doi: form.doi.trim(),
        abstract: form.abstractText.trim() || null,
        source: form.source || 'crossref',
      });
      setResult(res);
      setForm(null);
      setCandidates(null);
      toast.success(`文献已入库（#${res.paper_id}）`);
    } catch (e) {
      if (e instanceof LiteratureApiError) {
        setError({ message: e.message, existingPaperId: e.existingPaperId });
        if (e.status === 409 && e.existingPaperId) {
          // 409 与草稿 existing 同语义：同步到审核卡黄色提示并禁止再次确认
          setForm((prev) =>
            prev ? { ...prev, existing: true, existingPaperId: e.existingPaperId } : prev,
          );
        }
      }
      /* 网络层错误已由 api 层 toast */
    } finally {
      setConfirming(false);
    }
  };

  return (
    <Card>
      <CardContent className="space-y-4 p-4">
        <div className="text-sm text-muted-foreground">
          三种方式录入：DOI 直查 / 标题检索 Crossref / 上传 PDF 由 LLM 提取；
          核对（可修改）元数据后确认入库；仅入文献库，不入训练集、暂不入图谱。
        </div>

        {/* 第一步：三种录入方式 */}
        <Tabs
          value={mode}
          onValueChange={(v) => {
            setMode(v as 'doi' | 'title' | 'pdf');
            setError(null);
          }}
        >
          <TabsList>
            <TabsTrigger value="doi" disabled={searching || extracting || confirming}>
              DOI 查询
            </TabsTrigger>
            <TabsTrigger value="title" disabled={searching || extracting || confirming}>
              标题查询
            </TabsTrigger>
            <TabsTrigger value="pdf" disabled={searching || extracting || confirming}>
              上传 PDF
            </TabsTrigger>
          </TabsList>

          <TabsContent value="doi" className="pt-3">
            <div className="flex gap-2">
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !searching) void handleLookup();
                }}
                placeholder="粘贴 DOI（如 10.1021/jacs.1c00001 或 doi.org 链接）"
                className="flex-1"
                disabled={searching || confirming}
              />
              <Button onClick={() => void handleLookup()} disabled={searching || confirming}>
                {searching ? (
                  <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                ) : (
                  <Search className="mr-1.5 h-4 w-4" />
                )}
                {searching ? '查询中…' : '查询'}
              </Button>
            </div>
          </TabsContent>

          <TabsContent value="title" className="pt-3">
            <div className="flex gap-2">
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !searching) void handleLookup();
                }}
                placeholder="输入文献标题（Crossref 检索，返回前 3 候选）"
                className="flex-1"
                disabled={searching || confirming}
              />
              <Button onClick={() => void handleLookup()} disabled={searching || confirming}>
                {searching ? (
                  <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                ) : (
                  <Search className="mr-1.5 h-4 w-4" />
                )}
                {searching ? '查询中…' : '查询'}
              </Button>
            </div>
          </TabsContent>

          <TabsContent value="pdf" className="space-y-2 pt-3">
            <div className="flex gap-2">
              <Input
                type="file"
                accept=".pdf"
                className="flex-1"
                disabled={extracting || confirming}
                onChange={(e) => {
                  setPdfFile(e.target.files?.[0] ?? null);
                  setError(null);
                }}
              />
              <Button
                onClick={() => void handleExtractPdf()}
                disabled={extracting || confirming || !pdfFile}
              >
                {extracting ? (
                  <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                ) : (
                  <FileUp className="mr-1.5 h-4 w-4" />
                )}
                {extracting ? '提取中…' : '上传并提取'}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              {extracting
                ? 'LLM 正在阅读 PDF，约 10-30 秒…'
                : '适用于 Crossref 查不到或网络不通的文献；需 PDF 有文本层（扫描件请改用 DOI/标题），≤20MB。'}
            </p>
          </TabsContent>
        </Tabs>

        {/* 内联错误（404 / 502 / 409 等） */}
        {error && (
          <div className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              {error.message}
              {error.existingPaperId && (
                <span className="ml-1 font-medium">（已存在 #{error.existingPaperId}）</span>
              )}
            </span>
          </div>
        )}

        {/* 第二步 A：标题查询候选列表 */}
        {candidates && !form && !result && (
          <div className="space-y-2">
            <p className="text-sm font-medium text-foreground">
              找到 {candidates.length} 条候选，请选择要录入的文献：
            </p>
            <ul className="space-y-2">
              {candidates.map((c, i) => (
                <li key={`${c.doi}-${i}`}>
                  <button
                    type="button"
                    onClick={() => {
                      setForm(draftToForm(c));
                      setCandidates(null);
                    }}
                    className="w-full rounded-lg border border-border bg-muted/40 px-3 py-2 text-left transition-colors hover:border-primary/50 hover:bg-muted"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className="line-clamp-2 break-words text-sm font-medium text-foreground">
                        {c.title || '（无标题）'}
                      </span>
                      {c.existing && (
                        <Badge
                          variant="outline"
                          className="shrink-0 border-amber-300 text-amber-700 dark:border-amber-800 dark:text-amber-400"
                        >
                          库中已有 #{c.existing_paper_id}
                        </Badge>
                      )}
                    </div>
                    <DraftMetaLine d={c} />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* 第二步 B：审核卡（全字段可编辑） */}
        {form && (
          <div className="space-y-3 rounded-lg border border-border bg-muted/30 p-3">
            <div className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-2 text-sm font-medium text-foreground">
                核对并编辑文献信息
                {form.source === 'pdf-llm' && (
                  <Badge variant="outline" className="font-normal">
                    PDF LLM 提取{form.pdfFilename ? ` · ${form.pdfFilename}` : ''}
                  </Badge>
                )}
              </span>
              <Button variant="ghost" size="sm" onClick={() => reset(false)}>
                <RotateCcw className="mr-1 h-3.5 w-3.5" /> 重新查询
              </Button>
            </div>

            {/* 已存在：黄色提示并禁止确认 */}
            {form.existing && (
              <div className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>
                  库中已有此文献（#{form.existingPaperId ?? '未知'}），无需重复入库；
                  可点「重新查询」换一篇。
                </span>
              </div>
            )}

            <div className="space-y-1.5">
              <Label htmlFor="lit-title">标题</Label>
              <Textarea
                id="lit-title"
                value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })}
                rows={2}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="lit-authors">作者（每行一位）</Label>
              <Textarea
                id="lit-authors"
                value={form.authorsText}
                onChange={(e) => setForm({ ...form, authorsText: e.target.value })}
                rows={3}
                placeholder="Alice Wang&#10;Bob Li"
              />
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="space-y-1.5 sm:col-span-1">
                <Label htmlFor="lit-journal">期刊</Label>
                <Input
                  id="lit-journal"
                  value={form.journal}
                  onChange={(e) => setForm({ ...form, journal: e.target.value })}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="lit-year">年份</Label>
                <Input
                  id="lit-year"
                  value={form.yearText}
                  onChange={(e) => setForm({ ...form, yearText: e.target.value })}
                  inputMode="numeric"
                  placeholder="2024"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="lit-doi">DOI</Label>
                <Input
                  id="lit-doi"
                  value={form.doi}
                  onChange={(e) => setForm({ ...form, doi: e.target.value })}
                  placeholder="10.xxxx/…"
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="lit-abstract">摘要</Label>
              <Textarea
                id="lit-abstract"
                value={form.abstractText}
                onChange={(e) => setForm({ ...form, abstractText: e.target.value })}
                rows={4}
              />
            </div>

            <div className="flex items-center justify-end gap-2">
              <Button variant="outline" onClick={() => reset(false)} disabled={confirming}>
                取消
              </Button>
              <Button
                onClick={() => void handleConfirm()}
                disabled={confirming || form.existing || !form.title.trim()}
                title={form.existing ? '库中已有此文献，禁止重复入库' : undefined}
              >
                {confirming ? (
                  <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                ) : (
                  <BookPlus className="mr-1.5 h-4 w-4" />
                )}
                {confirming ? '入库中…' : '确认入库'}
              </Button>
            </div>
          </div>
        )}

        {/* 第三步：成功面板 */}
        {result && (
          <div className="space-y-2 rounded-lg border border-green-300 bg-green-50 px-3 py-2.5 text-sm text-green-800 dark:border-green-800 dark:bg-green-950/40 dark:text-green-300">
            <div className="flex items-center gap-2 font-medium">
              <CheckCircle2 className="h-4 w-4 shrink-0" />
              已入库，文献编号 #{result.paper_id}
            </div>
            <p className="text-xs">
              该文献仅入文献库：不入训练集、暂不入图谱（GraphRAG），收藏夹引用与助手检索现在即可解析它。
              {result.url && (
                <>
                  {' '}
                  <a
                    href={result.url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-primary underline-offset-2 hover:underline"
                    onClick={(e) => {
                      e.preventDefault();
                      openExternal(result.url!);
                    }}
                  >
                    {result.url}
                  </a>
                </>
              )}
            </p>
            <Button variant="outline" size="sm" onClick={() => reset(true)}>
              继续录入下一篇
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
