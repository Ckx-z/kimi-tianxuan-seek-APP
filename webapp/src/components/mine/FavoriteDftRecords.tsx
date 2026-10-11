/**
 * 档案卡「DFT 计算记录」区块（v220）
 *
 * 职责（只读写收藏的 dft_entries，不碰 DFT 计算逻辑）：
 * - 无记录 → 显示「DFT 未计算」+「与其他分子计算结合能」跳转按钮（预填单体）；
 * - 有记录 → 每条展示 标号（A/B/C…）/ 方法档位 / 结合能（kcal 主、kJ 次）/
 *   时间 / 模式（二聚体·任意双分子）/ 失败状态；点击记录 → 跳 DFT 页用
 *   ?job=<job_id> 打开该次计算的完整结果；
 * - 失败/无效记录可删除（二次确认，删除后持久化，标号不重排）。
 */
import { useState } from 'react';
import { useNavigate } from 'react-router';
import { toast } from 'sonner';
import { Atom, FlaskConical, Trash2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
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
import { formatDateTime } from '@/lib/format';
import { deleteDftEntry, type DftEntryItem, type FavoriteItem } from './api';

/** DFT 方法档位中文标签（psi4/xtb 两档统一口径） */
export function dftMethodLabel(method?: string): string {
  if (method === 'gfn2') return 'GFN2-xTB（精确）';
  if (method === 'gfnff') return 'GFN-FF 力场（快速）';
  if (method === 'wb97xd3bj_svp') return 'ωB97X-D3BJ/def2-SVP（真 DFT）';
  if (method === 'wb97xd3bj_svp_quick') return 'ωB97X-D3BJ/def2-SV(P)（批量快速档）';
  if (method === 'b3lyp_631gdp') return 'B3LYP/6-31G(d,p)（文献口径）';
  return method || '未知方法';
}

/** 条目计算模式：conditions.mode 优先，回落条目 mode；缺省按二聚体模式 */
export function entryMode(entry: DftEntryItem): 'dimer' | 'pair' {
  const fromConditions = entry.conditions?.mode;
  const raw = typeof fromConditions === 'string' ? fromConditions : entry.mode;
  return raw === 'pair' ? 'pair' : 'dimer';
}

/** 条目是否失败：error 字段非空，或缺少有效结合能（含被取消/中断的痕迹） */
export function entryFailed(entry: DftEntryItem): boolean {
  if (typeof entry.error === 'string' && entry.error.trim()) return true;
  const err = entry.err ?? entry.error_message ?? entry.fail_reason;
  if (typeof err === 'string' && err.trim()) return true;
  return typeof entry.e_bind_kcal !== 'number' && typeof entry.e_bind_kj !== 'number';
}

/** 标号序列：0→A … 25→Z，26→AA，27→AB…（与后端 store._label_at 同口径） */
export function labelAt(index: number): string {
  let n = Math.max(Math.floor(index), 0) + 1;
  let letters = '';
  while (n > 0 && letters.length < 3) {
    const rem = (n - 1) % 26;
    letters = String.fromCharCode(65 + rem) + letters;
    n = Math.floor((n - 1) / 26);
  }
  return letters || 'A';
}

/** 标号显示口径：条目自带 label > 后端派生数组 > 前端按条件指纹分组派生 */
export function entryLabels(entries: DftEntryItem[], provided?: string[]): string[] {
  const seq: Record<string, number> = {};
  return entries.map((entry, i) => {
    if (typeof entry.label === 'string' && entry.label.trim()) return entry.label.trim();
    const fromServer = provided?.[i];
    if (typeof fromServer === 'string' && fromServer.trim()) return fromServer.trim();
    const key = entry.condition_key
      || `${entryMode(entry)}|${entry.method ?? ''}|${entry.x_smiles ?? ''}`;
    seq[key] = (seq[key] ?? 0) + 1;
    return labelAt(seq[key] - 1);
  });
}

/** 失败/中断原因（用于展示，缺省给出通用文案） */
function failReason(entry: DftEntryItem): string {
  for (const v of [entry.error, entry.err, entry.error_message, entry.fail_reason]) {
    if (typeof v === 'string' && v.trim()) return v.trim();
  }
  return '该次计算未得到结合能（失败 / 已取消 / 已中断）';
}

/** 模式徽章：二聚体（醛胺缩合二聚体·X）/ 任意双分子（A···B 直接结合） */
function ModeBadge({ entry }: { entry: DftEntryItem }) {
  const mode = entryMode(entry);
  return mode === 'pair' ? (
    <Badge
      variant="secondary"
      className="text-[10px]"
      title="任意双分子模式（A···B 直接结合）"
    >
      任意双分子
    </Badge>
  ) : (
    <Badge variant="outline" className="text-[10px]" title="二聚体模式（醛胺缩合二聚体·X）">
      二聚体
    </Badge>
  );
}

/** 单条 DFT 记录：标号 + 方法 + 结合能 + 时间 + 模式 + 失败状态 + 删除 */
function DftRecordRow({
  entry,
  index,
  label,
  onOpen,
  onDelete,
  deleting,
}: {
  entry: DftEntryItem;
  index: number;
  label: string;
  onOpen: () => void;
  onDelete: () => void;
  deleting: boolean;
}) {
  const failed = entryFailed(entry);
  const time = formatDateTime(entry.created_at);
  const kcal = typeof entry.e_bind_kcal === 'number' ? entry.e_bind_kcal : null;
  const kj = typeof entry.e_bind_kj === 'number'
    ? entry.e_bind_kj
    : kcal !== null
      ? kcal * 4.184
      : null;
  return (
    <div
      className={`flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border px-3 py-2 text-sm ${
        failed ? 'border-destructive/40 bg-destructive/5' : 'border-gold/40 bg-gold-muted/40'
      }`}
    >
      <button
        type="button"
        className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-1.5 text-left"
        title={
          entry.job_id
            ? '点击查看该次计算的完整结果（跳转 DFT 计算页）'
            : '该记录未保存任务 id，点击到 DFT 计算页按该组单体重新查看'
        }
        onClick={onOpen}
      >
        <span
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-gold/60 bg-background text-xs font-bold text-gold-foreground"
          title={`该组第 ${index + 1} 条记录（标号 ${label}）`}
        >
          {label}
        </span>
        <span className="text-xs font-medium text-foreground">
          {dftMethodLabel(entry.method)}
          {entry.backend === 'psi4' ? '（Psi4 精度档）' : ''}
        </span>
        <ModeBadge entry={entry} />
        {failed ? (
          <Badge variant="outline" className="border-destructive/40 text-[10px] text-destructive">
            失败
          </Badge>
        ) : (
          <span className="tabular-nums">
            {kcal !== null && (
              <span className="font-semibold text-foreground">{kcal.toFixed(2)} kcal/mol</span>
            )}
            {kj !== null && (
              <span className="ml-1.5 text-xs text-muted-foreground">（{kj.toFixed(1)} kJ/mol）</span>
            )}
          </span>
        )}
        <span className="text-xs text-muted-foreground">{time || '未知时间'}</span>
        {failed && (
          <span className="w-full break-words text-xs text-destructive/90" title={failReason(entry)}>
            {failReason(entry)}
          </span>
        )}
      </button>
      <Button
        variant="ghost"
        size="sm"
        className="h-7 shrink-0 px-2 text-xs text-muted-foreground hover:text-destructive"
        title="删除该条 DFT 记录（失败记录也可删除；已有标号不重排）"
        disabled={deleting}
        onClick={(e) => {
          e.stopPropagation();
          onDelete();
        }}
      >
        <Trash2 className="h-3.5 w-3.5" />
      </Button>
    </div>
  );
}

export function DftRecordsSection({
  fav,
  onFavUpdated,
}: {
  fav: FavoriteItem;
  /** 记录删除后通知父级（详情 + 列表同步） */
  onFavUpdated?: (fav: FavoriteItem) => void;
}) {
  const navigate = useNavigate();
  const entries = fav.dft_entries ?? [];
  const labels = entryLabels(entries, fav.dft_labels);
  const [pendingDelete, setPendingDelete] = useState<{ entry: DftEntryItem; index: number } | null>(null);
  const [deleting, setDeleting] = useState(false);

  /** 跳 DFT 计算页：带上该组单体（a/b + 名称）与来源标记，尽量预填 */
  const jumpToDft = (jobId?: string, extra?: Record<string, string>) => {
    const params = new URLSearchParams();
    if (fav.aldehyde?.smiles) params.set('a', fav.aldehyde.smiles);
    if (fav.amine?.smiles) params.set('b', fav.amine.smiles);
    if (fav.aldehyde?.name) params.set('an', fav.aldehyde.name);
    if (fav.amine?.name) params.set('bn', fav.amine.name);
    params.set('fav', fav.id);
    params.set('from', 'favorite');
    if (jobId) params.set('job', jobId);
    for (const [k, v] of Object.entries(extra ?? {})) params.set(k, v);
    navigate(`/toolbox/dft?${params.toString()}`);
  };

  async function confirmDelete() {
    if (!pendingDelete) return;
    setDeleting(true);
    try {
      const updated = await deleteDftEntry(fav.id, pendingDelete.index);
      toast.success(
        `已删除 DFT 记录${labels[pendingDelete.index] ? ` ${labels[pendingDelete.index]}` : ''}`,
      );
      setPendingDelete(null);
      onFavUpdated?.(updated);
    } catch {
      /* 错误提示已由 api 层弹出 */
    } finally {
      setDeleting(false);
    }
  }

  return (
    <section>
      <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-foreground">
        <Atom className="h-4 w-4 text-gold" /> DFT 计算记录
        {entries.length > 0 && (
          <Badge variant="secondary" className="ml-1">{entries.length} 条</Badge>
        )}
      </h3>

      {entries.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border bg-card px-3 py-3 text-sm text-muted-foreground">
          DFT 未计算
        </p>
      ) : (
        <div className="space-y-2">
          {entries.map((entry, i) => (
            <DftRecordRow
              key={`${entry.job_id ?? 'nojob'}-${i}`}
              entry={entry}
              index={i}
              label={labels[i] ?? labelAt(i)}
              deleting={deleting}
              onOpen={() => jumpToDft(entry.job_id)}
              onDelete={() => setPendingDelete({ entry, index: i })}
            />
          ))}
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => jumpToDft()}
          disabled={!fav.aldehyde?.smiles || !fav.amine?.smiles}
          title="跳转 DFT 计算页并预填该组单体，可选择其他分子继续计算结合能"
        >
          <FlaskConical className="mr-1 h-3.5 w-3.5" /> 与其他分子计算结合能
        </Button>
        {fav.aldehyde?.smiles && fav.amine?.smiles && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => jumpToDft()}
            title="跳转 DFT 计算页并预填该组单体"
          >
            去 DFT 页重新计算
          </Button>
        )}
        <span className="text-xs text-muted-foreground">
          条件（方法档位 / 构象 / 采样数 / X 物质 / 模式）不同会自动新增一条记录，
          标号按 A、B、C…顺延；点击记录可查看该次完整结果，失败记录可直接删除。
        </span>
      </div>

      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(v) => !v && setPendingDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              确认删除 DFT 记录 {pendingDelete ? labels[pendingDelete.index] ?? '' : ''}？
            </AlertDialogTitle>
            <AlertDialogDescription>
              将删除「{dftMethodLabel(pendingDelete?.entry.method)}」这次计算记录
              {pendingDelete?.entry.job_id ? `（任务 ${pendingDelete.entry.job_id}）` : ''}
              。删除后不可恢复；其余记录标号保持不变（不会重排，可能出现空档）。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                void confirmDelete();
              }}
              disabled={deleting}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {deleting ? '删除中…' : '确认删除'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
