/**
 * 「GNN 模型演进」面板（v1.8.0，需求一）——设置页
 * 1. 环境状态：dphuanjing 训练环境可用性（缺失置灰）
 * 2. 反馈队列：打分纠错/文献 PDF/实验 CSV 三通道入队，确认/拒绝/改标签/删除
 * 3. 重训：启动微调 job + 阶段进度（data_parse→feature_build→fine_tune→
 *    guard→done）+ 取消 + 日志尾
 * 4. 版本管理：激活/回退 + 新旧版本对比（反馈对逐对 + 金标准指标）
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertTriangle, CheckCircle2, FileUp, FlaskConical, Loader2, Play,
  RotateCcw, Trash2, XCircle,
} from 'lucide-react';
import { toast } from 'sonner';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';

const BASE = '/api/gnn';

// ---------- 类型 ----------

interface GnnEnv {
  available: boolean;
  gnn_python: string | null;
  reason: string;
  active_version: string;
}

interface FeedbackRow {
  feedback_id: string;
  source: string;
  ald_smiles: string;
  amine_smiles: string;
  label: number;
  note: string;
  can_network: boolean;
  dedupe: Record<string, unknown>;
  status: 'pending' | 'confirmed' | 'rejected' | 'conflict';
  created_at: string;
}

interface GnnJob {
  job_id: string;
  version: string;
  status: string;
  phase: string;
  feedback_count?: number;
  epoch?: number;
  train_loss?: number;
  val_pr_auc?: number;
  best_pr_auc?: number;
  passed?: boolean;
  error?: string;
  log_tail?: string[];
  created_at?: string;
}

interface GnnVersion {
  version: string;
  base?: string;
  status: string;
  created_at?: string;
  val_pr_auc?: number | null;
  feedback_count?: number;
  gold?: Record<string, number | null> | null;
  meta?: Record<string, unknown>;
  guard?: Record<string, unknown>;
}

interface PdfCandidate {
  aldehyde_smiles: string;
  amine_smiles: string;
  label: number | null;
  evidence: string;
}

const SOURCE_LABEL: Record<string, string> = {
  score_correction: '打分纠错',
  literature_pdf: '文献 PDF',
  experiment_csv: '实验反馈',
};

const STATUS_BADGE: Record<string, { text: string; cls: string }> = {
  pending: { text: '待确认', cls: 'border-border bg-muted text-muted-foreground' },
  confirmed: { text: '已确认', cls: 'border-emerald-400 bg-emerald-50 text-emerald-700' },
  conflict: { text: '标签冲突', cls: 'border-amber-400 bg-amber-50 text-amber-700' },
  rejected: { text: '已拒绝', cls: 'border-border bg-muted text-muted-foreground' },
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
    throw new Error('无法连接后端服务');
  }
  if (!res.ok) {
    let message = `请求失败（${res.status}）`;
    try {
      const data = await res.json();
      if (typeof data?.detail === 'string') message = data.detail;
    } catch {
      /* keep */
    }
    throw new Error(message);
  }
  return (await res.json()) as T;
}

// ---------- 主组件 ----------

export function GnnEvolutionPanel() {
  const [env, setEnv] = useState<GnnEnv | null>(null);
  const [feedback, setFeedback] = useState<FeedbackRow[]>([]);
  const [confirmedCount, setConfirmedCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [jobs, setJobs] = useState<GnnJob[]>([]);
  const [versions, setVersions] = useState<GnnVersion[]>([]);
  const [activeVersion, setActiveVersion] = useState('gnn_v5.4');

  // 导入（PDF 候选预览）
  const [pdfOpen, setPdfOpen] = useState(false);
  const [pdfBusy, setPdfBusy] = useState(false);
  const [candidates, setCandidates] = useState<(PdfCandidate & { id: number })[]>([]);
  const [candidateLabels, setCandidateLabels] = useState<Record<number, number>>({});
  const [candidateNotes, setCandidateNotes] = useState<Record<number, string>>({});
  const pdfInputRef = useRef<HTMLInputElement>(null);
  const csvInputRef = useRef<HTMLInputElement>(null);

  // 编辑/删除
  const [editTarget, setEditTarget] = useState<FeedbackRow | null>(null);
  const [editLabel, setEditLabel] = useState<string>('1.0');
  const [editNote, setEditNote] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<FeedbackRow | null>(null);

  // 版本对比
  const [compareOpen, setCompareOpen] = useState(false);
  const [compareBusy, setCompareBusy] = useState(false);
  const [compareData, setCompareData] = useState<{
    version: string; pairs: Record<string, unknown>[];
    gold: { gnn_v5_4?: unknown; target?: unknown };
  } | null>(null);

  const refreshAll = useCallback(async (silent = false) => {
    try {
      const [e, f, j, v] = await Promise.all([
        req<GnnEnv>('/env'),
        req<{ feedback: FeedbackRow[]; count: number; confirmed: number }>('/feedback'),
        req<{ jobs: GnnJob[] }>('/retrain'),
        req<{ active: string; versions: GnnVersion[] }>('/versions'),
      ]);
      setEnv(e);
      setFeedback(f.feedback);
      setConfirmedCount(f.confirmed);
      setJobs(j.jobs);
      setVersions(v.versions);
      setActiveVersion(v.active);
    } catch (err) {
      if (!silent) toast.error(err instanceof Error ? err.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshAll();
    // 有运行中 job 时每 6 秒轮询
    const timer = setInterval(async () => {
      try {
        const j = await req<{ jobs: GnnJob[] }>('/retrain');
        setJobs(j.jobs);
        const running = j.jobs.some((x) => x.status === 'running');
        if (!running) {
          const v = await req<{ active: string; versions: GnnVersion[] }>('/versions');
          setVersions(v.versions);
          setActiveVersion(v.active);
        }
      } catch {
        /* 静默 */
      }
    }, 6000);
    return () => clearInterval(timer);
  }, [refreshAll]);

  const confirmRow = async (row: FeedbackRow) => {
    try {
      const rec = await req<FeedbackRow>(`/feedback/${row.feedback_id}/confirm`, { method: 'POST' });
      toast.success(rec.status === 'conflict' ? '该组合存在标签冲突，请复核' : '反馈已确认');
      await refreshAll(true);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '确认失败');
    }
  };

  const rejectRow = async (row: FeedbackRow) => {
    try {
      await req(`/feedback/${row.feedback_id}/reject`, { method: 'POST' });
      await refreshAll(true);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '操作失败');
    }
  };

  const saveEdit = async () => {
    if (!editTarget) return;
    try {
      await req(`/feedback/${editTarget.feedback_id}`, {
        method: 'PATCH',
        body: JSON.stringify({ label: Number(editLabel), note: editNote.trim() }),
      });
      toast.success('反馈已更新');
      setEditTarget(null);
      await refreshAll(true);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '更新失败');
    }
  };

  const doDelete = async () => {
    if (!deleteTarget) return;
    try {
      await req(`/feedback/${deleteTarget.feedback_id}`, { method: 'DELETE' });
      setDeleteTarget(null);
      await refreshAll(true);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '删除失败');
    }
  };

  const uploadCsv = async (file: File) => {
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await req<{ created: number; failed: unknown[] }>(
        '/feedback/import-table', { method: 'POST', body: form });
      toast.success(`实验反馈导入：新增 ${res.created} 条（失败 ${res.failed.length} 条）`);
      await refreshAll(true);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '导入失败');
    }
  };

  const uploadPdf = async (file: File) => {
    setPdfBusy(true);
    try {
      const form = new FormData();
      form.append('file', file);
      const res = await req<{
        filename: string; llm_used: boolean;
        candidates: PdfCandidate[]; candidate_count: number;
      }>('/feedback/import-pdf', { method: 'POST', body: form });
      setCandidates(res.candidates.map((c, i) => ({ ...c, id: i })));
      setCandidateLabels({});
      setCandidateNotes({});
      setPdfOpen(true);
      if (res.candidate_count === 0) {
        toast.warning('未从 PDF 中提取到候选体系（可改用手动提交反馈）');
      } else {
        toast.info(`提取到 ${res.candidate_count} 个候选体系（${res.llm_used ? 'LLM 结构化提取' : 'SMILES 正则扫描'}），请逐条确认`);
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'PDF 解析失败');
    } finally {
      setPdfBusy(false);
    }
  };

  const submitCandidates = async () => {
    try {
      const created: string[] = [];
      for (const c of candidates) {
        const label = candidateLabels[c.id];
        if (label == null) continue;
        const r = await req<{ feedback_id: string }>('/feedback', {
          method: 'POST',
          body: JSON.stringify({
            ald_smiles: c.aldehyde_smiles, amine_smiles: c.amine_smiles,
            label, note: candidateNotes[c.id] ?? c.evidence ?? '',
            source: 'literature_pdf',
          }),
        });
        created.push(r.feedback_id);
      }
      if (created.length > 0) {
        await req('/feedback/confirm-batch', {
          method: 'POST',
          body: JSON.stringify({ feedback_ids: created }),
        });
        toast.success(`已入库并确认 ${created.length} 条反馈`);
      }
      setPdfOpen(false);
      await refreshAll(true);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '提交失败');
    }
  };

  const startRetrain = async () => {
    try {
      const job = await req<GnnJob>('/retrain', {
        method: 'POST', body: JSON.stringify({}),
      });
      toast.success(`重训已启动：${job.version}`);
      await refreshAll(true);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '启动失败');
    }
  };

  const cancelJob = async (job: GnnJob) => {
    try {
      await req(`/retrain/${job.job_id}/cancel`, { method: 'POST' });
      await refreshAll(true);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '取消失败');
    }
  };

  const activate = async (version: string) => {
    try {
      await req(`/versions/${encodeURIComponent(version)}/activate`, { method: 'POST' });
      toast.success(`已切换到 ${version}（下一请求生效）`);
      await refreshAll(true);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '切换失败');
    }
  };

  const openCompare = async (version: string) => {
    setCompareOpen(true);
    setCompareBusy(true);
    setCompareData(null);
    try {
      const data = await req<typeof compareData>(`/versions/${encodeURIComponent(version)}/compare`);
      setCompareData(data);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '对比失败');
      setCompareOpen(false);
    } finally {
      setCompareBusy(false);
    }
  };

  const runningJob = jobs.find((j) => j.status === 'running') ?? null;
  const envOk = env?.available === true;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <FlaskConical className="h-4 w-4 text-gold" />
          GNN 模型演进（文献/实验反馈修正）
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> 加载中…
          </div>
        ) : (
          <>
            {/* 环境状态 */}
            <div
              className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-sm ${
                envOk
                  ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
                  : 'border-amber-300 bg-amber-50 text-amber-800'
              }`}
            >
              {envOk ? (
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
              ) : (
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              )}
              <span>
                {envOk
                  ? `训练环境就绪（${env?.gnn_python}）· 当前激活版本 ${activeVersion}`
                  : env?.reason || '训练环境不可用'}
              </span>
            </div>

            {/* 反馈队列 */}
            <div className="space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-medium">
                  反馈队列（已确认 {confirmedCount} 条，重训将合并这些样本）
                </p>
                <div className="flex gap-2">
                  <input
                    ref={csvInputRef}
                    type="file"
                    accept=".csv"
                    className="hidden"
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      e.target.value = '';
                      if (f) void uploadCsv(f);
                    }}
                  />
                  <Button size="sm" variant="outline" onClick={() => csvInputRef.current?.click()}>
                    <FileUp className="mr-1.5 h-3.5 w-3.5" />
                    导入实验 CSV
                  </Button>
                  <input
                    ref={pdfInputRef}
                    type="file"
                    accept=".pdf"
                    className="hidden"
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      e.target.value = '';
                      if (f) void uploadPdf(f);
                    }}
                  />
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={pdfBusy}
                    onClick={() => pdfInputRef.current?.click()}
                  >
                    {pdfBusy ? (
                      <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <FileUp className="mr-1.5 h-3.5 w-3.5" />
                    )}
                    导入文献 PDF
                  </Button>
                </div>
              </div>

              {feedback.length === 0 ? (
                <p className="rounded-lg border border-dashed p-4 text-center text-sm text-muted-foreground">
                  暂无反馈。可在打分页「反馈打分不合理」提交，或导入文献 PDF / 实验 CSV。
                </p>
              ) : (
                <div className="max-h-64 space-y-1.5 overflow-y-auto">
                  {feedback.map((f) => (
                    <div
                      key={f.feedback_id}
                      className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-muted/30 px-3 py-2 text-sm"
                    >
                      <Badge variant="outline" className={STATUS_BADGE[f.status]?.cls}>
                        {STATUS_BADGE[f.status]?.text ?? f.status}
                      </Badge>
                      <Badge variant="outline">{SOURCE_LABEL[f.source] ?? f.source}</Badge>
                      <Badge variant="outline">{f.label}</Badge>
                      <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground"
                            title={`${f.ald_smiles} + ${f.amine_smiles}`}>
                        {f.ald_smiles.slice(0, 22)}… + {f.amine_smiles.slice(0, 22)}…
                      </span>
                      {!f.can_network && f.label > 0 && (
                        <span title="该组合化学上不可成网（成网红线），label>0 请复核">
                          <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
                        </span>
                      )}
                      {f.status === 'conflict' && (
                        <span className="text-xs text-amber-600">与已确认标签冲突</span>
                      )}
                      <span className="max-w-40 truncate text-xs text-muted-foreground" title={f.note}>
                        {f.note || '（无理由）'}
                      </span>
                      {(f.status === 'pending' || f.status === 'conflict') && (
                        <>
                          <Button size="sm" variant="outline" className="h-7 px-2"
                                  onClick={() => void confirmRow(f)}>
                            <CheckCircle2 className="h-3.5 w-3.5" /> 确认
                          </Button>
                          <Button size="sm" variant="outline" className="h-7 px-2"
                                  onClick={() => void rejectRow(f)}>
                            <XCircle className="h-3.5 w-3.5" /> 拒绝
                          </Button>
                          <Button size="sm" variant="outline" className="h-7 px-2"
                                  onClick={() => {
                                    setEditTarget(f);
                                    setEditLabel(String(f.label));
                                    setEditNote(f.note);
                                  }}>
                            改
                          </Button>
                        </>
                      )}
                      <button
                        type="button"
                        title="删除反馈"
                        className="shrink-0 rounded p-1 text-muted-foreground hover:text-destructive"
                        onClick={() => setDeleteTarget(f)}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* 重训 */}
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  disabled={!envOk || confirmedCount === 0 || runningJob !== null}
                  onClick={() => void startRetrain()}
                  title={
                    !envOk
                      ? '训练环境不可用'
                      : confirmedCount === 0
                        ? '请先确认反馈样本'
                        : runningJob
                          ? '已有运行中的任务'
                          : undefined
                  }
                >
                  <Play className="mr-1.5 h-3.5 w-3.5" />
                  开始重训（冻结底层微调 + 验证闸门）
                </Button>
                {runningJob && (
                  <Button size="sm" variant="outline" onClick={() => void cancelJob(runningJob)}>
                    <XCircle className="mr-1.5 h-3.5 w-3.5" />
                    取消
                  </Button>
                )}
                <span className="text-xs text-muted-foreground">
                  tree 模型保持不动，作为基准对照；闸门不通过自动不激活。
                </span>
              </div>
              {runningJob && (
                <div className="space-y-1 rounded-lg border border-gold/40 bg-gold-muted/30 p-3 text-xs">
                  <p>
                    版本 {runningJob.version} · 阶段 {runningJob.phase}
                    {runningJob.epoch != null && ` · epoch ${runningJob.epoch}`}
                    {runningJob.val_pr_auc != null && ` · val PR-AUC ${runningJob.val_pr_auc}`}
                    {runningJob.best_pr_auc != null && ` · best ${runningJob.best_pr_auc}`}
                  </p>
                  {runningJob.log_tail && runningJob.log_tail.length > 0 && (
                    <pre className="max-h-28 overflow-y-auto whitespace-pre-wrap rounded bg-muted/50 p-2 font-mono text-[11px]">
                      {runningJob.log_tail.slice(-8).join('\n')}
                    </pre>
                  )}
                </div>
              )}
            </div>

            {/* 版本列表 */}
            <div className="space-y-2">
              <p className="text-sm font-medium">版本管理（激活 / 回退 / 对比）</p>
              <div className="space-y-1.5">
                <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/30 px-3 py-2 text-sm">
                  <Badge variant="outline"
                         className={activeVersion === 'gnn_v5.4'
                           ? 'border-gold bg-gold-muted text-gold-foreground' : ''}>
                    gnn_v5.4（基础）
                  </Badge>
                  <span className="flex-1 text-xs text-muted-foreground">
                    随包基线（全局虚拟节点 + Focal + Isotonic 校准）
                  </span>
                  {activeVersion !== 'gnn_v5.4' && (
                    <Button size="sm" variant="outline" className="h-7 px-2"
                            onClick={() => void activate('gnn_v5.4')}>
                      <RotateCcw className="mr-1 h-3 w-3" /> 回退
                    </Button>
                  )}
                </div>
                {versions.map((v) => (
                  <div key={v.version}
                       className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-muted/30 px-3 py-2 text-sm">
                    <Badge variant="outline"
                           className={activeVersion === v.version
                             ? 'border-gold bg-gold-muted text-gold-foreground' : ''}>
                      {v.version}
                    </Badge>
                    <Badge variant="outline">
                      {v.status === 'active' ? '激活中' : v.status === 'rejected' ? '已拒绝' : v.status}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      反馈 {v.feedback_count ?? '—'} 条
                      {v.val_pr_auc != null && ` · val PR-AUC ${v.val_pr_auc}`}
                      {v.gold?.spearman != null && ` · 金标准 Spearman ${v.gold.spearman}`}
                      {v.gold?.mae != null && ` · MAE ${v.gold.mae}`}
                    </span>
                    <span className="flex-1" />
                    {activeVersion !== v.version && v.status !== 'rejected' && (
                      <Button size="sm" variant="outline" className="h-7 px-2"
                              onClick={() => void activate(v.version)}>
                        激活
                      </Button>
                    )}
                    <Button size="sm" variant="outline" className="h-7 px-2"
                            onClick={() => void openCompare(v.version)}>
                      对比
                    </Button>
                  </div>
                ))}
                {versions.length === 0 && (
                  <p className="text-xs text-muted-foreground">
                    尚无微调版本：确认反馈后点「开始重训」生成第一个版本。
                  </p>
                )}
              </div>
            </div>
          </>
        )}

        {/* PDF 候选确认弹窗 */}
        <Dialog open={pdfOpen} onOpenChange={(o) => !o && setPdfOpen(false)}>
          <DialogContent className="max-w-2xl">
            <DialogHeader>
              <DialogTitle>确认文献提取的候选体系（{candidates.length}）</DialogTitle>
            </DialogHeader>
            <div className="max-h-[55vh] space-y-2 overflow-y-auto">
              {candidates.map((c) => (
                <div key={c.id} className="rounded-lg border border-border p-2 text-xs">
                  <p className="truncate font-mono" title={`${c.aldehyde_smiles} + ${c.amine_smiles}`}>
                    醛：{c.aldehyde_smiles.slice(0, 40)}… 胺：{c.amine_smiles.slice(0, 40)}…
                  </p>
                  <p className="mt-1 truncate text-muted-foreground" title={c.evidence}>
                    依据：{c.evidence || '（无）'}
                  </p>
                  <div className="mt-1.5 flex items-center gap-2">
                    <Select
                      value={candidateLabels[c.id] != null ? String(candidateLabels[c.id]) : ''}
                      onValueChange={(v) =>
                        setCandidateLabels((m) => ({ ...m, [c.id]: Number(v) }))}
                    >
                      <SelectTrigger className="h-7 w-36" aria-label="档位">
                        <SelectValue placeholder="选择档位" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="1">成膜 1.0</SelectItem>
                        <SelectItem value="0.5">边界 0.5</SelectItem>
                        <SelectItem value="0">不成膜 0.0</SelectItem>
                      </SelectContent>
                    </Select>
                    <input
                      className="h-7 min-w-0 flex-1 rounded-md border border-input bg-transparent px-2 text-xs"
                      placeholder="理由（默认取提取依据）"
                      value={candidateNotes[c.id] ?? ''}
                      onChange={(e) =>
                        setCandidateNotes((m) => ({ ...m, [c.id]: e.target.value }))}
                    />
                  </div>
                </div>
              ))}
              {candidates.length === 0 && (
                <p className="py-6 text-center text-sm text-muted-foreground">
                  未提取到候选体系，可关闭后手动提交。
                </p>
              )}
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setPdfOpen(false)}>取消</Button>
              <Button
                onClick={() => void submitCandidates()}
                disabled={!candidates.some((c) => candidateLabels[c.id] != null)}
              >
                入库并确认（已选档位的条目）
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* 编辑反馈弹窗 */}
        <Dialog open={editTarget !== null} onOpenChange={(o) => !o && setEditTarget(null)}>
          <DialogContent className="max-w-sm">
            <DialogHeader>
              <DialogTitle>修改反馈</DialogTitle>
            </DialogHeader>
            <div className="space-y-3">
              <div className="space-y-1.5">
                <Label>档位</Label>
                <Select value={editLabel} onValueChange={setEditLabel}>
                  <SelectTrigger aria-label="档位">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="1">成膜 1.0</SelectItem>
                    <SelectItem value="0.5">边界 0.5</SelectItem>
                    <SelectItem value="0">不成膜 0.0</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>理由</Label>
                <Textarea value={editNote} onChange={(e) => setEditNote(e.target.value)} rows={3} />
              </div>
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setEditTarget(null)}>取消</Button>
              <Button onClick={() => void saveEdit()}>保存</Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* 删除确认弹窗 */}
        <Dialog open={deleteTarget !== null} onOpenChange={(o) => !o && setDeleteTarget(null)}>
          <DialogContent className="max-w-sm">
            <DialogHeader>
              <DialogTitle>确认删除该反馈？</DialogTitle>
            </DialogHeader>
            <p className="text-sm text-muted-foreground">删除后不可恢复。</p>
            <DialogFooter>
              <Button variant="outline" onClick={() => setDeleteTarget(null)}>取消</Button>
              <Button className="bg-red-600 text-white hover:bg-red-700"
                      onClick={() => void doDelete()}>
                确认删除
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* 版本对比弹窗 */}
        <Dialog open={compareOpen} onOpenChange={(o) => !o && !compareBusy && setCompareOpen(false)}>
          <DialogContent className="max-w-3xl">
            <DialogHeader>
              <DialogTitle>版本对比：{compareData?.version ?? ''} vs gnn_v5.4</DialogTitle>
            </DialogHeader>
            {compareBusy ? (
              <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                正在计算（逐对 GNN 推理 + 39 对金标准，约 1–3 分钟）…
              </div>
            ) : compareData ? (
              <div className="max-h-[60vh] space-y-3 overflow-y-auto">
                {compareData.gold && (
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b text-left text-muted-foreground">
                        <th className="py-1 pr-2">金标准指标</th>
                        <th className="py-1 pr-2">gnn_v5.4</th>
                        <th className="py-1">目标版本</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(['a_min', 'c_max', 'mae', 'spearman'] as const).map((k) => (
                        <tr key={k} className="border-b">
                          <td className="py-1 pr-2 font-medium">{k}</td>
                          <td className="py-1 pr-2">
                            {(compareData.gold as { gnn_v5_4?: Record<string, unknown> }).gnn_v5_4?.[k] as string ?? '—'}
                          </td>
                          <td className="py-1">
                            {(compareData.gold as { target?: Record<string, unknown> }).target?.[k] as string ?? '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="py-1 pr-2">反馈组合</th>
                      <th className="py-1 pr-2">标签</th>
                      <th className="py-1 pr-2">gnn_v5.4</th>
                      <th className="py-1">目标版本</th>
                    </tr>
                  </thead>
                  <tbody>
                    {compareData.pairs.map((p, i) => (
                      <tr key={i} className="border-b">
                        <td className="max-w-52 truncate py-1 pr-2 font-mono"
                            title={`${p.ald} + ${p.amine}`}>
                          {(p.ald as string).slice(0, 14)}… + {(p.amine as string).slice(0, 14)}…
                        </td>
                        <td className="py-1 pr-2">{p.label as number}</td>
                        <td className="py-1 pr-2">
                          {p.gnn_v5_4 == null ? '—' : (p.gnn_v5_4 as number).toFixed(3)}
                        </td>
                        <td className="py-1">
                          {p.target == null ? '—' : (p.target as number).toFixed(3)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="py-6 text-center text-sm text-muted-foreground">无对比数据</p>
            )}
          </DialogContent>
        </Dialog>
      </CardContent>
    </Card>
  );
}
