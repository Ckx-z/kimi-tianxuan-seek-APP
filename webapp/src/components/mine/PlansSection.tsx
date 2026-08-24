/**
 * 「我的方案库」区块
 * - 方案模板管理：上传 docx 提取模板（可填名称）、自定义模板可删除（内置不可删）
 * - 方案卡列表：方案 vN + 模板名 + 时间
 * - 点击展开查看完整方案（单体 / 条件 / 步骤 简洁渲染）
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronDown, ClipboardList, FileUp, Loader2, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import {
  deletePlanTemplate,
  fetchPlanTemplates,
  uploadPlanTemplate,
  type PlanItem,
  type PlanTemplateItem,
} from './api';

/** 条件键名中文映射（未知键原样显示） */
const CONDITION_LABELS: Record<string, string> = {
  solvent: '溶剂',
  modulator: '调制剂',
  catalyst: '催化剂',
  temperature_c: '温度 (°C)',
  time_days: '时间 (天)',
  vessel: '容器',
};

/** 方案模板管理：列表 + 上传 docx 提取 + 删除自定义模板（内置不可删） */
function TemplatesManager() {
  const [templates, setTemplates] = useState<PlanTemplateItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [tplName, setTplName] = useState('');
  const [tplFile, setTplFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [toDelete, setToDelete] = useState<PlanTemplateItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    fetchPlanTemplates()
      .then(setTemplates)
      .catch(() => setTemplates([]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  /** 上传 docx → LLM 提取模板 → 列表即时刷新 */
  const handleUpload = async () => {
    if (!tplFile) {
      toast.warning('请先选择 .docx 文件');
      return;
    }
    if (!tplFile.name.toLowerCase().endsWith('.docx')) {
      toast.error('仅支持 .docx 文件');
      return;
    }
    setUploading(true);
    try {
      const tpl = await uploadPlanTemplate(tplFile, tplName);
      toast.success(`模板「${tpl.name}」提取成功，已加入模板库`);
      setDialogOpen(false);
      setTplName('');
      setTplFile(null);
      if (fileRef.current) fileRef.current.value = '';
      refresh();
    } catch {
      /* 错误提示已由 api 层弹出；对话框保持打开，已选文件不丢 */
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async () => {
    if (!toDelete) return;
    setDeleting(true);
    try {
      await deletePlanTemplate(toDelete.id);
      toast.success(`模板「${toDelete.name}」已删除`);
      setToDelete(null);
      refresh();
    } catch {
      /* 错误提示已由 api 层弹出 */
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-foreground">方案模板</h3>
        <Button size="sm" variant="outline" onClick={() => setDialogOpen(true)}>
          <FileUp className="mr-1.5 h-3.5 w-3.5" />
          添加方案模板
        </Button>
      </div>

      {loading ? (
        <Skeleton className="h-8 w-full" />
      ) : (
        <ul className="space-y-1.5">
          {templates.map((t) => (
            <li
              key={t.id}
              className="flex items-center justify-between gap-2 rounded-lg bg-muted/40 px-3 py-2 text-sm"
            >
              <div className="min-w-0">
                <span className="break-words font-medium text-foreground">{t.name}</span>
                {t.builtin ? (
                  <Badge variant="secondary" className="ml-2 shrink-0">内置</Badge>
                ) : (
                  <Badge variant="outline" className="ml-2 shrink-0 border-primary/40 text-primary">
                    自定义
                  </Badge>
                )}
                {t.source && (
                  <span className="ml-2 break-words text-xs text-muted-foreground">{t.source}</span>
                )}
              </div>
              {!t.builtin && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 shrink-0 text-muted-foreground hover:text-destructive"
                  title="删除该模板"
                  onClick={() => setToDelete(t)}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              )}
            </li>
          ))}
          {templates.length === 0 && (
            <li className="text-sm text-muted-foreground">暂无模板（内置模板加载失败时显示此提示）。</li>
          )}
        </ul>
      )}

      {/* 上传对话框 */}
      <Dialog
        open={dialogOpen}
        onOpenChange={(v) => {
          if (!uploading) setDialogOpen(v);
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>添加方案模板</DialogTitle>
            <DialogDescription>
              上传文献/实验方案的 .docx 文件，将由 LLM 自动提取为方案卡模板（条件 / 步骤 / 检查清单）。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label>模板名称（可选，缺省取文件名）</Label>
              <Input
                value={tplName}
                onChange={(e) => setTplName(e.target.value)}
                placeholder="如：界面法-低温变体"
                disabled={uploading}
              />
            </div>
            <div className="space-y-1.5">
              <Label>docx 文件</Label>
              <input
                ref={fileRef}
                type="file"
                accept=".docx"
                disabled={uploading}
                onChange={(e) => setTplFile(e.target.files?.[0] ?? null)}
                className="block w-full text-sm text-muted-foreground file:mr-3 file:rounded-md file:border file:border-border file:bg-background file:px-3 file:py-1.5 file:text-sm file:text-foreground hover:file:bg-accent"
              />
              {tplFile && (
                <p className="text-xs text-muted-foreground">
                  已选：{tplFile.name}（{(tplFile.size / 1024).toFixed(0)}KB）
                </p>
              )}
            </div>
            <Button
              className="w-full"
              disabled={uploading || !tplFile}
              onClick={() => void handleUpload()}
            >
              {uploading ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  LLM 提取中（可能需要几十秒）…
                </>
              ) : (
                '上传并提取模板'
              )}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* 删除确认 */}
      <AlertDialog open={toDelete !== null} onOpenChange={(v) => !v && setToDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除该模板？</AlertDialogTitle>
            <AlertDialogDescription>
              将删除自定义模板「{toDelete?.name}」。已生成的方案卡不受影响。此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => void handleDelete()}
              disabled={deleting}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {deleting ? '删除中…' : '确认删除'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/** 单个方案卡（可展开） */
function PlanRow({ plan }: { plan: PlanItem }) {
  const [open, setOpen] = useState(false);
  const card = plan.plan_card;
  const conditions = (card?.conditions ?? {}) as Record<string, unknown>;

  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card">
      {/* 折叠头：点击展开/收起 */}
      <button
        type="button"
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/50"
        onClick={() => setOpen((v) => !v)}
      >
        <div className="flex min-w-0 items-center gap-3">
          <Badge className="shrink-0 bg-primary text-primary-foreground">方案 v{plan.seq ?? '?'}</Badge>
          <span className="truncate text-sm font-medium text-foreground">
            {plan.template_name || card?.template || '未命名方案'}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <span className="text-xs text-muted-foreground">{plan.created_at || plan.plan_id}</span>
          <ChevronDown
            className={`h-4 w-4 text-muted-foreground transition-transform ${open ? 'rotate-180' : ''}`}
          />
        </div>
      </button>

      {/* 展开内容：完整方案 */}
      {open && (
        <div className="space-y-4 border-t border-border px-4 py-4">
          {!card ? (
            <p className="text-sm text-muted-foreground">该方案暂无方案卡内容。</p>
          ) : (
            <>
              {/* 单体 */}
              <div className="grid gap-3 sm:grid-cols-2">
                {(
                  [
                    ['醛单体', card.aldehyde],
                    ['胺单体', card.amine],
                  ] as const
                ).map(([label, m]) => (
                  <div key={label} className="rounded-lg border border-border bg-muted/40 p-3 text-sm">
                    <div className="text-xs text-muted-foreground">{label}</div>
                    <div className="mt-1 font-medium text-foreground">{m?.name || '—'}</div>
                    <div className="mt-1 break-all font-mono text-xs text-muted-foreground">
                      {m?.smiles || '—'}
                    </div>
                  </div>
                ))}
              </div>

              {/* 条件 */}
              {Object.keys(conditions).length > 0 && (
                <div>
                  <div className="mb-1.5 text-xs font-semibold text-muted-foreground">成膜条件</div>
                  <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    {Object.entries(conditions).map(([k, v]) => (
                      <div key={k} className="rounded-lg bg-gold-muted/40 px-3 py-2 text-sm">
                        <span className="text-xs text-muted-foreground">
                          {CONDITION_LABELS[k] ?? k}：
                        </span>
                        <span className="text-foreground">{String(v)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* 步骤 */}
              {Array.isArray(card.steps) && card.steps.length > 0 && (
                <div>
                  <div className="mb-1.5 text-xs font-semibold text-muted-foreground">操作步骤</div>
                  <ol className="list-decimal space-y-1 pl-5 text-sm text-foreground">
                    {card.steps.map((s, i) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ol>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

export function PlansSection({ plans, loading }: { plans: PlanItem[]; loading: boolean }) {
  const manager = <TemplatesManager />;

  if (loading) {
    return (
      <div className="space-y-3">
        {manager}
        {[0, 1].map((i) => (
          <Skeleton key={i} className="h-14 w-full rounded-xl" />
        ))}
      </div>
    );
  }

  if (plans.length === 0) {
    return (
      <div className="space-y-3">
        {manager}
        <div className="rounded-xl border border-dashed border-border bg-card p-12 text-center text-sm text-muted-foreground">
          <ClipboardList className="mx-auto mb-2 h-6 w-6 text-muted-foreground" />
          暂无方案：在迭代页采纳建议后，方案会保存在这里。
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {manager}
      {plans.map((p) => (
        <PlanRow key={p.plan_id} plan={p} />
      ))}
    </div>
  );
}
