/**
 * 「我的收藏」收藏夹视图
 * - 左侧收藏夹列表（名称+数量；窄屏横向滚动不溢出），右侧当前夹收藏卡片网格
 * - 收藏夹操作：新建（对话框输名称，重名 400 中文提示）、改名、
 *   删除两步确认（第一次说明将删 N 条收藏，第二次输入夹名/再次确认，不可恢复）
 * - 卡片：醛名 × 胺名大字 + SMILES 小字 + 打分徽章 + DFT 徽章（本期预留「DFT 未计算」）
 *   + 创建时间 + 删除按钮
 * - 点击卡片弹出详情 Dialog：单体信息 / 预测快照 / 性质卡 / 方案卡 / 文献列表 /
 *   该组实验记录列表（行可点击 → 嵌套放大记录详情，带「返回」回到收藏详情）
 * - 删除收藏经 AlertDialog 确认后调用 DELETE /api/favorites/{id}
 * - 响应式：Dialog 限高 + 固定头部 + 内部滚动；窄屏下结构图/性质卡/表格自动换行、横向滚动
 */
import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from 'react';
import { useNavigate } from 'react-router';
import { Trash2, BookOpen, FlaskConical, ChevronRight, FolderPlus, Pencil, Atom, FolderInput, CopyPlus, ExternalLink } from 'lucide-react';
import { toast } from 'sonner';
import { doiUrl, openExternal } from '@/lib/external';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import FavoriteFolderDialog from '@/components/common/FavoriteFolderDialog';
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  deleteFavorite,
  fetchFavorite,
  fetchFolders,
  createFolder,
  renameFolder,
  deleteFolder,
  updateFavorite,
  copyFavorite,
  type DftEntryItem,
  type FavoriteItem,
  type FolderItem,
  type PredictionSnapshot,
  type ReferenceItem,
} from './api';
// 实验记录类型/接口与详情对话框复用记录页组件（单一类型口径，嵌套放大共用）
import {
  listRecords,
  type RecordItem,
} from '@/components/records/api';
import RecordDetailDialog from '@/components/records/RecordDetailDialog';
import { OUTCOME_META } from '@/components/records/meta';
// 只读复用查询打分页的结果组件与 API（不修改其文件），保证放大详情与查询打分页内容一致
import ResultCard from '@/components/query/ResultCard';
import MonomerPropsCard from '@/components/query/MonomerPropsCard';
import PlanCardPanel from '@/components/query/PlanCardPanel';
import {
  fetchMonomerProps,
  fetchPlanCard,
  fetchPlanTemplates,
  predictPair,
  type MonomerProps,
  type PlanCardData,
  type PlanTemplateItem,
  type PredictResult,
} from '@/components/query/api';

/** 截断 SMILES 显示 */
function shortSmiles(s?: string, n = 42): string {
  if (!s) return '—';
  return s.length > n ? `${s.slice(0, n)}…` : s;
}

/** 结合能 kJ/mol 主口径：e_bind_kj 缺失时用 kcal×4.184 换算兜底 */
function eBindKj(entry: { e_bind_kj?: number | null; e_bind_kcal?: number | null }): number | null {
  if (typeof entry.e_bind_kj === 'number') return entry.e_bind_kj;
  if (typeof entry.e_bind_kcal === 'number') return entry.e_bind_kcal * 4.184;
  return null;
}

/** 结合能主+次显示（kJ/mol 大字 + kcal/mol 小字灰色），digits 控制小数位 */
function EBindText({
  entry,
  mainClass = 'text-lg',
}: {
  entry: { e_bind_kj?: number | null; e_bind_kcal?: number | null };
  mainClass?: string;
}) {
  const kj = eBindKj(entry);
  if (kj === null) return null;
  const bound = kj < 0;
  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-1.5">
      <span
        className={`font-bold tabular-nums ${mainClass} ${
          bound ? 'text-green-700 dark:text-green-400' : 'text-red-700 dark:text-red-400'
        }`}
      >
        {kj.toFixed(1)} kJ/mol
      </span>
      {typeof entry.e_bind_kcal === 'number' && (
        <span className="text-xs tabular-nums text-muted-foreground">
          （{entry.e_bind_kcal.toFixed(2)} kcal/mol）
        </span>
      )}
    </span>
  );
}

/** 预测分徽章（金色系） */
function ScoreBadge({ fav }: { fav: FavoriteItem }) {
  const score = fav.latest_prediction?.score;
  if (typeof score !== 'number') {
    return (
      <Badge variant="outline" className="border-border bg-muted text-muted-foreground">
        未打分
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="border-gold/60 bg-gold-muted text-gold-foreground">
      {score.toFixed(2)} 分
    </Badge>
  );
}

/** DFT 方法档位中文标签 */
function dftMethodLabel(method?: string): string {
  if (method === 'gfn2') return 'GFN2-xTB（精确）';
  if (method === 'gfnff') return 'GFN-FF 力场（快速）';
  if (method === 'wb97xd3bj_svp') return 'ωB97X-D3BJ/def2-SVP（Psi4 真 DFT）';
  return method || '未知方法';
}

/** DFT 徽章：以 dft_entries 最新一条为准（dft_snapshot 兜底）；结合能 kJ/mol 主显示 */
function DftBadge({ fav }: { fav: FavoriteItem }) {
  const entries = fav.dft_entries ?? [];
  const latest = entries.length > 0 ? entries[entries.length - 1] : fav.dft_snapshot;
  const kj = latest ? eBindKj(latest) : null;
  if (latest && kj !== null) {
    return (
      <Badge
        variant="outline"
        className="cursor-pointer border-gold/60 bg-gold-muted text-gold-foreground"
        title="点击查看 DFT 计算结果"
      >
        结合能 {kj.toFixed(1)} kJ/mol
        {entries.length > 1 ? `（${entries.length} 条）` : ''}
      </Badge>
    );
  }
  if (latest) {
    return (
      <Badge variant="outline" className="border-primary/40 text-primary">
        DFT 已计算{entries.length > 1 ? `（${entries.length} 条）` : ''}
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="border-border bg-muted text-muted-foreground">
      DFT 未计算
    </Badge>
  );
}

/** 匹配类型中文标签 */
function matchLabel(t?: string): string {
  return t === 'both' ? '醛胺同报道' : t === 'aldehyde' ? '报道过该醛' : t === 'amine' ? '报道过该胺' : '相关';
}

/** 文献列表（标题截断+悬浮全文；DOI 渲染为可点击外链，缺失时灰字「暂无 DOI」） */
function ReferenceList({ refs }: { refs?: ReferenceItem[] }) {
  if (!refs || refs.length === 0) {
    return <p className="text-sm text-muted-foreground">暂无关联文献</p>;
  }
  return (
    <ul className="space-y-2">
      {refs.map((r, i) => {
        const link = r.url ?? doiUrl(r.doi);
        return (
          <li
            key={`${r.paper_id ?? r.title}-${i}`}
            className="flex flex-wrap items-start justify-between gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2"
          >
            <div className="min-w-0 flex-1">
              <div
                className="line-clamp-2 break-words text-sm font-medium text-foreground"
                title={r.title || '未命名文献'}
              >
                {r.title || '未命名文献'}
              </div>
              <div className="mt-0.5 text-xs text-muted-foreground">
                {r.note || matchLabel(r.match_type)}
                {typeof r.count === 'number' ? ` · 出现 ${r.count} 次` : ''}
              </div>
              <div className="mt-0.5 text-xs">
                {link ? (
                  <a
                    href={link}
                    target="_blank"
                    rel="noreferrer"
                    title={`在浏览器中打开 ${link}`}
                    onClick={(e) => {
                      e.preventDefault();
                      openExternal(link);
                    }}
                    className="inline-flex items-center gap-0.5 break-all text-primary underline-offset-2 hover:underline"
                  >
                    <ExternalLink className="h-3 w-3 shrink-0" />
                    DOI: {r.doi || link.replace(/^https?:\/\/doi\.org\//i, '')}
                  </a>
                ) : (
                  <span className="text-muted-foreground/70">暂无 DOI</span>
                )}
              </div>
            </div>
            <Badge variant="outline" className="shrink-0 border-primary/40 text-primary">
              {matchLabel(r.match_type)}
            </Badge>
          </li>
        );
      })}
    </ul>
  );
}

/** 单体 2D 结构图（/api/monomers/structure.svg，失败时静默隐藏） */
function StructureImg({ smiles, label }: { smiles?: string; label: string }) {
  const [failed, setFailed] = useState(false);
  if (!smiles || failed) return null;
  return (
    <img
      src={`/api/monomers/structure.svg?smiles=${encodeURIComponent(smiles)}`}
      alt={`${label}结构图`}
      onError={() => setFailed(true)}
      className="mt-2 h-28 w-full rounded-md border border-border bg-white object-contain dark:bg-white/95"
    />
  );
}

/** 收藏快照 → 查询打分页 ResultCard 的 PredictResult（口径对齐） */
function snapshotToResult(p?: PredictionSnapshot | null): PredictResult | null {
  if (!p) return null;
  const oodRaw = p.ood;
  const ood =
    typeof oodRaw === 'string'
      ? { level: oodRaw || 'none', reasons: [] as string[] }
      : { level: oodRaw?.level ?? 'none', reasons: oodRaw?.reasons ?? [] };
  const hasScore = typeof p.score === 'number';
  const hasSub = typeof p.tree_score === 'number' || typeof p.gnn_score === 'number';
  if (!hasScore && !hasSub && ood.level !== 'out') return null;
  const num = (v: unknown): number | null => (typeof v === 'number' ? v : null);
  return {
    score: num(p.score),
    score_policy: p.score_policy ?? 'max_tree_gnn',
    tree_score: num(p.tree_score),
    tree_std: num(p.tree_std) ?? num(p.std),
    tree_model_name: p.tree_model_name ?? p.arm ?? null,
    tree_route: p.tree_route ?? null,
    gnn_score: num(p.gnn_score),
    gnn_std: num(p.gnn_std),
    ood,
  };
}

/** 单侧性质卡状态 */
interface PropsState {
  loading: boolean;
  error: string | null;
  data: MonomerProps | null;
}
const emptyProps: PropsState = { loading: false, error: null, data: null };

/** 二聚体结构图：优先后端预渲染 dimer_svg，否则按 SMILES 走 structure.svg，再兜底文本 */
function DimerFigure({ entry }: { entry: DftEntryItem }) {
  // dimer_svg 为后端预渲染可信 SVG（dangerouslySetInnerHTML 无加载错误概念，
  // 不需要失败回退状态）
  if (entry.dimer_svg) {
    return (
      <div
        className="mt-1 max-h-32 overflow-hidden rounded-md border border-border bg-white p-1 dark:bg-white/95 [&>svg]:mx-auto [&>svg]:max-h-28"
        // 后端预渲染的可信 SVG
        dangerouslySetInnerHTML={{ __html: entry.dimer_svg }}
      />
    );
  }
  if (entry.dimer_smiles) {
    return (
      <>
        <StructureImg smiles={entry.dimer_smiles} label="缩合二聚体" />
        <code className="mt-1 block break-all rounded border bg-muted/50 px-2 py-1 font-mono text-[11px] text-muted-foreground">
          {entry.dimer_smiles}
        </code>
      </>
    );
  }
  return <p className="mt-1 text-xs text-muted-foreground">（未保存二聚体信息）</p>;
}

/** 单条 DFT 记录卡片（dft_entries 条目） */
function DftEntryCard({ entry, index }: { entry: DftEntryItem; index: number }) {
  const time = entry.created_at ? String(entry.created_at).replace('T', ' ').slice(0, 19) : '';
  return (
    <div className="rounded-lg border border-gold/40 bg-gold-muted/40 p-3 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-medium text-muted-foreground">
          第 {index + 1} 条 · {dftMethodLabel(entry.method)}
          {time ? ` · ${time}` : ''}
        </span>
        <EBindText entry={entry} />
      </div>
      <div className="mt-2 grid gap-3 sm:grid-cols-2">
        <div className="min-w-0">
          <div className="text-xs text-muted-foreground">缩合二聚体</div>
          <DimerFigure entry={entry} />
        </div>
        <div className="min-w-0">
          <div className="text-xs text-muted-foreground">
            X 物质{entry.x_description ? `（${entry.x_description}）` : ''}
          </div>
          {entry.x_smiles ? (
            <>
              <StructureImg smiles={entry.x_smiles} label="X 物质" />
              <code className="mt-1 block break-all rounded border bg-muted/50 px-2 py-1 font-mono text-[11px] text-muted-foreground">
                {entry.x_smiles}
              </code>
            </>
          ) : (
            <p className="mt-1 text-xs text-muted-foreground">（未保存 X SMILES）</p>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * DFT 计算记录（详情弹窗内）：分条展示 dft_entries（二聚体结构图 / X 物质 /
 * 结合能 kJ/mol 主显示 / 方法 / 时间）。
 * 旧收藏只有 dft_snapshot 的按单条显示并标「旧版口径」徽章。
 * 底部按钮：「继续计算其他物质」「重新计算」均跳转 DFT 页并预填该组单体。
 */
function DftSummarySection({ fav, onRecalc }: { fav: FavoriteItem; onRecalc: () => void }) {
  const navigate = useNavigate();
  const entries = fav.dft_entries ?? [];
  const snap = fav.dft_snapshot;
  if (entries.length === 0 && !snap) return null;

  /** 跳转 DFT 页并预填两个单体 SMILES/名称（from=favorite 标记来源） */
  const handleJump = () => {
    const params = new URLSearchParams();
    if (fav.aldehyde?.smiles) params.set('a', fav.aldehyde.smiles);
    if (fav.amine?.smiles) params.set('b', fav.amine.smiles);
    if (fav.aldehyde?.name) params.set('an', fav.aldehyde.name);
    if (fav.amine?.name) params.set('bn', fav.amine.name);
    params.set('from', 'favorite');
    onRecalc();
    navigate(`/toolbox/dft?${params.toString()}`);
  };

  return (
    <section>
      <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-foreground">
        <Atom className="h-4 w-4 text-gold" /> DFT 计算记录
        {entries.length > 1 && (
          <Badge variant="secondary" className="ml-1">{entries.length} 条</Badge>
        )}
      </h3>

      {entries.length > 0 ? (
        /* 新口径：分条列表 */
        <div className="space-y-3">
          {entries.map((entry, i) => (
            <DftEntryCard key={entry.job_id ?? i} entry={entry} index={i} />
          ))}
        </div>
      ) : snap ? (
        /* 旧收藏：仅 dft_snapshot，按单条显示并标「旧版口径」 */
        <div className="rounded-lg border border-gold/40 bg-gold-muted/40 p-3 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-medium text-muted-foreground">
              {dftMethodLabel(snap.method)}
              {snap.date ? ` · ${String(snap.date).replace('T', ' ').slice(0, 19)}` : ''}
            </span>
            <div className="flex items-center gap-2">
              <Badge
                variant="outline"
                className="border-amber-300 text-amber-700 dark:border-amber-800 dark:text-amber-400"
                title="该收藏保存于 DFT 分条记录上线前，仅有一条快照"
              >
                旧版口径
              </Badge>
              <EBindText entry={snap} />
            </div>
          </div>
          <div className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted-foreground">
            <span>
              HOMO-LUMO 能隙（复合物）：
              <span className="tabular-nums text-foreground">
                {typeof snap.gap_ev?.complex === 'number' ? `${snap.gap_ev.complex.toFixed(2)} eV` : '—'}
              </span>
            </span>
            <span>
              偶极矩（复合物）：
              <span className="tabular-nums text-foreground">
                {typeof snap.dipole_debye?.complex === 'number'
                  ? `${snap.dipole_debye.complex.toFixed(2)} Debye`
                  : '—'}
              </span>
            </span>
          </div>
          {snap.dimer_smiles ? (
            <div className="mt-2 space-y-1.5">
              <div className="text-xs text-muted-foreground">
                计算对象：缩合二聚体
                {snap.x_description ? ` 与 X（${snap.x_description}）` : ''}
              </div>
              <code className="block break-all rounded border bg-muted/50 px-2 py-1 font-mono text-[11px]">
                {snap.dimer_smiles}
              </code>
              <StructureImg smiles={snap.dimer_smiles} label="缩合二聚体" />
            </div>
          ) : (
            <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">
              旧版计算口径（两单体结合能）：DFT 2.0 起计算对象为缩合二聚体与 X，
              可点下方「重新计算」获取新口径结果。
            </p>
          )}
        </div>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={handleJump}
          disabled={!fav.aldehyde?.smiles || !fav.amine?.smiles}
          title="跳转 DFT 计算页并预填该组合单体，可选择其他 X 物质继续计算"
        >
          继续计算其他物质
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={handleJump}
          disabled={!fav.aldehyde?.smiles || !fav.amine?.smiles}
          title="跳转 DFT 计算页并预填该组合单体"
        >
          重新计算
        </Button>
        <span className="text-xs text-muted-foreground">
          半经验结果仅供相对比较，精确能量请在 DFT 页导出输入文件复算。
        </span>
      </div>
    </section>
  );
}

/**
 * 收藏详情 Dialog（与查询打分页结果内容一致：分数/OOD/结构图/性质卡/方案卡）
 * 内嵌「该组实验记录」列表：点击记录行 → 嵌套放大记录详情（带返回）。
 */
function FavoriteDetailDialog({
  fav,
  open,
  onOpenChange,
  onFavUpdated,
}: {
  fav: FavoriteItem | null;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  /** 一键打分后收藏快照已更新（父级同步详情与列表） */
  onFavUpdated?: (fav: FavoriteItem) => void;
}) {
  const [records, setRecords] = useState<RecordItem[]>([]);
  const [recLoading, setRecLoading] = useState(false);
  /** 嵌套放大的实验记录（非空时在最上层展示记录详情，「返回」回到本对话框） */
  const [nestedRec, setNestedRec] = useState<RecordItem | null>(null);
  const [aldProps, setAldProps] = useState<PropsState>(emptyProps);
  const [amineProps, setAmineProps] = useState<PropsState>(emptyProps);
  const [planCard, setPlanCard] = useState<PlanCardData | null>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const [templates, setTemplates] = useState<PlanTemplateItem[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(false);
  const [templateId, setTemplateId] = useState('');

  // 打开时拉取：实验记录 + 性质卡×2 + 方案卡模板/方案卡（与查询页联动口径一致）
  useEffect(() => {
    if (!open || !fav) return;
    let cancelled = false;
    setNestedRec(null);
    setRecLoading(true);
    listRecords(fav.id)
      .then((list) => !cancelled && setRecords(list))
      .catch(() => !cancelled && setRecords([]))
      .finally(() => !cancelled && setRecLoading(false));

    const loadProps = (
      smiles: string | undefined,
      name: string | undefined,
      setter: Dispatch<SetStateAction<PropsState>>,
    ) => {
      if (!smiles) {
        setter(emptyProps);
        return;
      }
      setter({ loading: true, error: null, data: null });
      fetchMonomerProps(smiles, name ?? '')
        .then((data) => !cancelled && setter({ loading: false, error: null, data }))
        .catch((e) =>
          !cancelled &&
          setter({ loading: false, error: e instanceof Error ? e.message : '未知错误', data: null }),
        );
    };
    loadProps(fav.aldehyde?.smiles, fav.aldehyde?.name, setAldProps);
    loadProps(fav.amine?.smiles, fav.amine?.name, setAmineProps);

    setTemplatesLoading(true);
    fetchPlanTemplates()
      .then((list) => !cancelled && setTemplates(list))
      .catch(() => !cancelled && setTemplates([]))
      .finally(() => !cancelled && setTemplatesLoading(false));

    setTemplateId('');
    if (fav.aldehyde?.smiles && fav.amine?.smiles) {
      setPlanLoading(true);
      setPlanError(null);
      setPlanCard(null);
      fetchPlanCard({
        aldehyde_smiles: fav.aldehyde.smiles,
        amine_smiles: fav.amine.smiles,
        ald_name: fav.aldehyde.name ?? '',
        amine_name: fav.amine.name ?? '',
        template_id: null,
      })
        .then((card) => !cancelled && setPlanCard(card))
        .catch((e) => !cancelled && setPlanError(e instanceof Error ? e.message : '未知错误'))
        .finally(() => !cancelled && setPlanLoading(false));
    }
    return () => {
      cancelled = true;
    };
  }, [open, fav]);

  /** 切换模板 → 重新生成方案卡（与查询页一致） */
  const handleTemplateChange = (id: string) => {
    setTemplateId(id);
    if (!fav?.aldehyde?.smiles || !fav?.amine?.smiles) return;
    setPlanLoading(true);
    setPlanError(null);
    fetchPlanCard({
      aldehyde_smiles: fav.aldehyde.smiles,
      amine_smiles: fav.amine.smiles,
      ald_name: fav.aldehyde.name ?? '',
      amine_name: fav.amine.name ?? '',
      template_id: id || null,
    })
      .then((card) => setPlanCard(card))
      .catch((e) => setPlanError(e instanceof Error ? e.message : '未知错误'))
      .finally(() => setPlanLoading(false));
  };

  const handleTemplateUploaded = (tpl: PlanTemplateItem) => {
    setTemplates((prev) => [...prev.filter((t) => t.id !== tpl.id), tpl]);
    handleTemplateChange(tpl.id);
  };

  /** 一键打分：调 /api/predict，后端自动回写该单体对所有收藏的快照 */
  const [quickScoring, setQuickScoring] = useState(false);
  const handleQuickScore = async () => {
    if (!fav?.aldehyde?.smiles || !fav?.amine?.smiles) return;
    setQuickScoring(true);
    try {
      await predictPair(fav.aldehyde.smiles, fav.amine.smiles);
      const updated = await fetchFavorite(fav.id);
      toast.success('打分完成，收藏分数已更新');
      onFavUpdated?.(updated);
    } catch {
      /* 错误提示已由 api 层弹出 */
    } finally {
      setQuickScoring(false);
    }
  };

  if (!fav) return null;
  const result = snapshotToResult(fav.latest_prediction);

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="flex max-h-[90dvh] w-[calc(100vw-1.5rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-4xl">
          <DialogHeader className="border-b border-border px-5 py-4">
            <DialogTitle className="text-left text-lg leading-snug text-gradient-royal">
              {fav.aldehyde?.name || '未知醛'} × {fav.amine?.name || '未知胺'}
            </DialogTitle>
            <DialogDescription className="text-left">
              收藏编号 {fav.id} · 创建于 {fav.created_at || '未知时间'}
            </DialogDescription>
          </DialogHeader>

          <div className="min-h-0 flex-1 space-y-6 overflow-y-auto px-5 py-4">
            {/* 单体信息（含 2D 结构图） */}
            <section>
              <h3 className="mb-2 text-sm font-semibold text-foreground">单体信息</h3>
              <div className="grid gap-3 sm:grid-cols-2">
                {(
                  [
                    ['醛单体', fav.aldehyde],
                    ['胺单体', fav.amine],
                  ] as const
                ).map(([label, m]) => (
                  <div key={label} className="min-w-0 rounded-lg border border-border bg-muted/40 p-3 text-sm">
                    <div className="text-xs text-muted-foreground">{label}</div>
                    <div className="mt-1 break-words font-medium text-foreground">{m?.name || '—'}</div>
                    <div className="mt-1 break-all font-mono text-xs text-muted-foreground">
                      {m?.smiles || '—'}
                    </div>
                    {m?.cas && <div className="mt-1 text-xs text-muted-foreground">CAS: {m.cas}</div>}
                    <StructureImg smiles={m?.smiles} label={label} />
                  </div>
                ))}
              </div>
              {fav.notes && (
                <p className="mt-2 break-words rounded-lg bg-gold-muted/40 px-3 py-2 text-xs text-muted-foreground">
                  备注：{fav.notes}
                </p>
              )}
            </section>

            {/* 打分结果（复用查询页 ResultCard：主分数 + 树/GNN 分量 + OOD 横幅） */}
            <section>
              {result ? (
                <ResultCard result={result} loading={false} />
              ) : (
                <div className="space-y-3 rounded-xl border border-dashed border-border bg-card p-8 text-center text-sm text-muted-foreground">
                  <p>
                    尚未打分：可在查询页对该组合进行预测；打分后此处显示与查询打分页一致的完整结果。
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={quickScoring || !fav.aldehyde?.smiles || !fav.amine?.smiles}
                    onClick={() => void handleQuickScore()}
                  >
                    {quickScoring ? '打分中…' : '一键打分'}
                  </Button>
                </div>
              )}
            </section>

            {/* DFT 计算结果摘要（有 dft_snapshot 时展示；「重新计算」跳 DFT 页预填单体） */}
            <DftSummarySection fav={fav} onRecalc={() => onOpenChange(false)} />

            {/* 单体性质卡（复用查询页 MonomerPropsCard：RDKit facts + LLM 解读） */}
            <section>
              <h3 className="mb-2 text-sm font-semibold text-foreground">单体性质</h3>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <MonomerPropsCard
                  title="醛单体性质"
                  name={fav.aldehyde?.name || undefined}
                  loading={aldProps.loading}
                  error={aldProps.error}
                  props={aldProps.data}
                />
                <MonomerPropsCard
                  title="胺单体性质"
                  name={fav.amine?.name || undefined}
                  loading={amineProps.loading}
                  error={amineProps.error}
                  props={amineProps.data}
                />
              </div>
            </section>

            {/* 方案卡（复用查询页 PlanCardPanel，可切换模板） */}
            <section className="min-w-0">
              <PlanCardPanel
                card={planCard}
                loading={planLoading}
                error={planError}
                templates={templates}
                templatesLoading={templatesLoading}
                templateId={templateId}
                onTemplateChange={handleTemplateChange}
                onTemplateUploaded={handleTemplateUploaded}
                disabled={!fav.aldehyde?.smiles || !fav.amine?.smiles}
              />
            </section>

            {/* 文献列表 */}
            <section>
              <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-foreground">
                <BookOpen className="h-4 w-4 text-gold" /> 参考文献
              </h3>
              <ReferenceList refs={fav.references} />
            </section>

            {/* 该组实验记录（行可点击 → 嵌套放大详情） */}
            <section>
              <h3 className="mb-2 flex items-center gap-1.5 text-sm font-semibold text-foreground">
                <FlaskConical className="h-4 w-4 text-gold" /> 该组实验记录
                {records.length > 0 && (
                  <Badge variant="secondary" className="ml-1">{records.length}</Badge>
                )}
              </h3>
              {recLoading ? (
                <div className="space-y-2">
                  <Skeleton className="h-8 w-full" />
                  <Skeleton className="h-8 w-full" />
                </div>
              ) : records.length === 0 ? (
                <p className="text-sm text-muted-foreground">该收藏下暂无实验记录</p>
              ) : (
                <div className="overflow-x-auto rounded-lg border border-border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="whitespace-nowrap">编号</TableHead>
                        <TableHead className="whitespace-nowrap">日期</TableHead>
                        <TableHead className="whitespace-nowrap">结果</TableHead>
                        <TableHead className="whitespace-nowrap">成膜强度</TableHead>
                        <TableHead className="whitespace-nowrap">操作人</TableHead>
                        <TableHead className="w-8" />
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {records.map((r) => {
                        const meta = OUTCOME_META[r.outcome ?? ''] ?? OUTCOME_META.failed;
                        return (
                          <TableRow
                            key={r.record_id}
                            className="cursor-pointer hover:bg-muted/60"
                            title="点击放大查看该记录详情"
                            onClick={() => setNestedRec(r)}
                          >
                            <TableCell className="whitespace-nowrap font-medium">
                              {r.experiment_no || r.record_id}
                              {r.status === 'draft' && (
                                <Badge
                                  variant="outline"
                                  className="ml-1.5 border-gold/60 text-gold-foreground"
                                >
                                  草稿
                                </Badge>
                              )}
                            </TableCell>
                            <TableCell className="whitespace-nowrap">{r.date || '—'}</TableCell>
                            <TableCell>
                              <Badge className={meta.className}>{meta.label}</Badge>
                            </TableCell>
                            <TableCell className="whitespace-nowrap">{r.strength || '—'}</TableCell>
                            <TableCell className="whitespace-nowrap">{r.operator || '—'}</TableCell>
                            <TableCell>
                              <ChevronRight className="h-4 w-4 text-muted-foreground" />
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
              )}
              {records.length > 0 && (
                <p className="mt-1.5 text-xs text-muted-foreground">
                  点击任意记录行可放大查看完整详情（含自我总结与失误）。
                </p>
              )}
            </section>
          </div>
        </DialogContent>
      </Dialog>

      {/* 嵌套：实验记录放大详情（「返回」回到收藏详情，不关闭整个链路） */}
      <RecordDetailDialog
        rec={nestedRec}
        onClose={() => setNestedRec(null)}
        onBack={() => setNestedRec(null)}
        onChanged={(updated) => {
          setNestedRec(updated);
          setRecords((prev) =>
            prev.map((r) => (r.record_id === updated.record_id ? updated : r)),
          );
        }}
      />
    </>
  );
}

/** 收藏夹视图主组件：左侧收藏夹列表（窄屏横向滚动）+ 右侧当前夹卡片网格 */
export function FavoritesSection({
  favorites,
  loading,
  onChanged,
}: {
  favorites: FavoriteItem[];
  loading: boolean;
  /** 删除/移夹成功后通知父组件刷新 */
  onChanged: () => void;
}) {
  const [detail, setDetail] = useState<FavoriteItem | null>(null);
  const [toDelete, setToDelete] = useState<FavoriteItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  /** 一键打分进行中（按收藏 id 记） */
  const [scoringId, setScoringId] = useState<string | null>(null);
  /** 移动 / 复制目标收藏（非空时弹出收藏夹选择对话框） */
  const [moveCopyTarget, setMoveCopyTarget] = useState<{ fav: FavoriteItem; mode: 'move' | 'copy' } | null>(null);
  const [moveCopying, setMoveCopying] = useState(false);

  // ---------- 收藏夹状态 ----------
  const [folders, setFolders] = useState<FolderItem[]>([]);
  const [foldersLoading, setFoldersLoading] = useState(true);
  const [currentFolderId, setCurrentFolderId] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [renameTarget, setRenameTarget] = useState<FolderItem | null>(null);
  const [renameName, setRenameName] = useState('');
  const [renaming, setRenaming] = useState(false);
  /** 删除收藏夹两步确认：step 1 说明将删 N 条；step 2 输入名称/再次确认 */
  const [folderDelete, setFolderDelete] = useState<FolderItem | null>(null);
  const [deleteStep, setDeleteStep] = useState<1 | 2>(1);
  const [deleteConfirmText, setDeleteConfirmText] = useState('');
  const [deletingFolder, setDeletingFolder] = useState(false);

  /** 加载收藏夹列表；保持当前选中（已被删除则回退到第一个夹） */
  const loadFolders = useCallback(async () => {
    try {
      const list = await fetchFolders();
      setFolders(list);
      setCurrentFolderId((prev) =>
        prev && list.some((f) => f.id === prev) ? prev : (list[0]?.id ?? ''),
      );
    } catch {
      /* 错误提示已由 api 层弹出 */
    } finally {
      setFoldersLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadFolders();
  }, [loadFolders]);

  // 收藏增删（父级 onChanged 刷新 favorites）→ 同步夹内计数
  useEffect(() => {
    if (!foldersLoading) void loadFolders();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [favorites.length]);

  /** 当前夹的收藏（夹列表未加载完成时显示全部，避免闪烁空态） */
  const visibleFavorites = currentFolderId
    ? favorites.filter((f) => f.folder_id === currentFolderId)
    : favorites;

  /** 旧收藏无分数字段时的一键打分：打分后快照由后端回写，随后刷新 */
  async function quickScore(fav: FavoriteItem) {
    if (!fav.aldehyde?.smiles || !fav.amine?.smiles) {
      toast.error('该收藏缺少单体 SMILES，无法打分');
      return;
    }
    setScoringId(fav.id);
    try {
      await predictPair(fav.aldehyde.smiles, fav.amine.smiles);
      toast.success('打分完成，收藏分数已更新');
      onChanged();
    } catch {
      /* 错误提示已由 api 层弹出 */
    } finally {
      setScoringId(null);
    }
  }

  async function confirmDelete() {
    if (!toDelete) return;
    setDeleting(true);
    try {
      await deleteFavorite(toDelete.id);
      toast.success(`已删除收藏 ${toDelete.id}`);
      setToDelete(null);
      onChanged();
    } catch {
      /* 错误已由 api 层 toast */
    } finally {
      setDeleting(false);
    }
  }

  /** 移动 / 复制确认：移动走 PATCH folder_id，复制走 copy 端点（实验记录不随复制转移） */
  async function handleMoveCopy(folderId: string, folderName: string) {
    const target = moveCopyTarget;
    if (!target) return;
    setMoveCopying(true);
    try {
      if (target.mode === 'move') {
        await updateFavorite(target.fav.id, { folder_id: folderId });
        toast.success(`已移动到收藏夹「${folderName}」`);
      } else {
        await copyFavorite(target.fav.id, folderId);
        toast.success(`已复制到收藏夹「${folderName}」（实验记录不随复制转移）`);
      }
      setMoveCopyTarget(null);
      onChanged();
    } catch {
      /* 错误已由 api 层 toast */
    } finally {
      setMoveCopying(false);
    }
  }

  // ---------- 收藏夹操作 ----------

  async function handleCreateFolder() {
    const name = newFolderName.trim();
    if (!name) {
      toast.error('请输入收藏夹名称');
      return;
    }
    setCreatingFolder(true);
    try {
      const folder = await createFolder(name);
      toast.success(`已创建收藏夹「${folder.name}」`);
      setCreateOpen(false);
      setNewFolderName('');
      await loadFolders();
      setCurrentFolderId(folder.id);
    } catch {
      /* 错误已由 api 层 toast（含重名 400 中文提示） */
    } finally {
      setCreatingFolder(false);
    }
  }

  async function handleRenameFolder() {
    if (!renameTarget) return;
    const name = renameName.trim();
    if (!name) {
      toast.error('请输入收藏夹名称');
      return;
    }
    setRenaming(true);
    try {
      await renameFolder(renameTarget.id, name);
      toast.success(`已改名为「${name}」`);
      setRenameTarget(null);
      await loadFolders();
    } catch {
      /* 错误已由 api 层 toast（含重名 400 中文提示） */
    } finally {
      setRenaming(false);
    }
  }

  async function handleDeleteFolder() {
    if (!folderDelete) return;
    const count = folderDelete.favorite_count ?? 0;
    if (count > 0 && deleteConfirmText.trim() !== folderDelete.name) return;
    setDeletingFolder(true);
    try {
      const n = await deleteFolder(folderDelete.id);
      toast.success(
        n > 0
          ? `已删除收藏夹「${folderDelete.name}」及其中 ${n} 条收藏`
          : `已删除收藏夹「${folderDelete.name}」`,
      );
      setFolderDelete(null);
      setDeleteStep(1);
      setDeleteConfirmText('');
      await loadFolders();
      onChanged();
    } catch {
      /* 错误已由 api 层 toast（如最后一个夹 400 保护） */
    } finally {
      setDeletingFolder(false);
    }
  }

  if (loading) {
    return (
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-36 w-full rounded-xl" />
        ))}
      </div>
    );
  }

  const deleteCount = folderDelete?.favorite_count ?? 0;

  return (
    <>
      <div className="flex flex-col gap-4 md:flex-row">
        {/* 收藏夹列表：窄屏横向滚动条，md+ 固定侧栏 */}
        <aside className="shrink-0 md:w-52">
          <div className="mb-2 flex items-center justify-between gap-2">
            <span className="text-sm font-medium text-muted-foreground">收藏夹</span>
            <Button
              variant="outline"
              size="sm"
              className="h-7 shrink-0 px-2 text-xs"
              onClick={() => {
                setNewFolderName('');
                setCreateOpen(true);
              }}
            >
              <FolderPlus className="mr-1 h-3.5 w-3.5" /> 新建
            </Button>
          </div>
          {foldersLoading ? (
            <div className="flex gap-2 md:flex-col">
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-9 w-full" />
            </div>
          ) : folders.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              暂无收藏夹，点击「新建」创建；旧收藏会在加载时自动归入「收藏夹1」。
            </p>
          ) : (
            <div className="flex gap-2 overflow-x-auto pb-1 md:flex-col md:overflow-visible md:pb-0">
              {folders.map((folder) => {
                const active = folder.id === currentFolderId;
                return (
                  <div
                    key={folder.id}
                    className={`flex shrink-0 items-center gap-1.5 rounded-lg border px-3 py-2 text-sm transition-colors ${
                      active
                        ? 'border-primary/50 bg-primary/5 text-foreground'
                        : 'border-border bg-card text-muted-foreground hover:bg-muted/60'
                    }`}
                  >
                    <button
                      type="button"
                      className="min-w-0 flex-1 text-left"
                      onClick={() => setCurrentFolderId(folder.id)}
                    >
                      <span className="block truncate">{folder.name}</span>
                    </button>
                    <Badge variant="secondary" className="shrink-0 px-1.5 text-xs">
                      {folder.favorite_count ?? 0}
                    </Badge>
                    {active && (
                      <>
                        <button
                          type="button"
                          title="改名"
                          className="shrink-0 text-muted-foreground hover:text-foreground"
                          onClick={() => {
                            setRenameTarget(folder);
                            setRenameName(folder.name);
                          }}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        <button
                          type="button"
                          title="删除收藏夹"
                          className="shrink-0 text-muted-foreground hover:text-destructive"
                          onClick={() => {
                            setFolderDelete(folder);
                            setDeleteStep(1);
                            setDeleteConfirmText('');
                          }}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </aside>

        {/* 当前夹的收藏卡片网格 */}
        <div className="min-w-0 flex-1">
          {favorites.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border bg-card p-12 text-center text-sm text-muted-foreground">
              暂无收藏：在查询页预测后可点击「收藏」将组合加入这里。
            </div>
          ) : visibleFavorites.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border bg-card p-12 text-center text-sm text-muted-foreground">
              该收藏夹暂无收藏。
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {visibleFavorites.map((fav) => (
                <Card
                  key={fav.id}
                  className="cursor-pointer transition-shadow hover:shadow-md hover:shadow-primary/10"
                  onClick={() => setDetail(fav)}
                >
                  <CardContent className="space-y-2 p-4">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0 break-words text-base font-semibold leading-snug text-foreground">
                        {fav.aldehyde?.name || '未知醛'}
                        <span className="mx-1 text-gold">×</span>
                        {fav.amine?.name || '未知胺'}
                      </div>
                      <div className="flex shrink-0 items-center">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7 text-muted-foreground hover:text-primary"
                          title="移动到其他收藏夹"
                          onClick={(e) => {
                            e.stopPropagation(); // 阻止触发卡片点击
                            setMoveCopyTarget({ fav, mode: 'move' });
                          }}
                        >
                          <FolderInput className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7 text-muted-foreground hover:text-primary"
                          title="复制到其他收藏夹"
                          onClick={(e) => {
                            e.stopPropagation(); // 阻止触发卡片点击
                            setMoveCopyTarget({ fav, mode: 'copy' });
                          }}
                        >
                          <CopyPlus className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7 text-muted-foreground hover:text-destructive"
                          title="删除收藏"
                          onClick={(e) => {
                            e.stopPropagation(); // 阻止触发卡片点击
                            setToDelete(fav);
                          }}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>
                    <div className="break-all font-mono text-xs text-muted-foreground">
                      {shortSmiles(fav.aldehyde?.smiles)}
                    </div>
                    <div className="break-all font-mono text-xs text-muted-foreground">
                      {shortSmiles(fav.amine?.smiles)}
                    </div>
                    <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <ScoreBadge fav={fav} />
                        <DftBadge fav={fav} />
                      </div>
                      {typeof fav.latest_prediction?.score !== 'number' && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-6 shrink-0 px-2 text-xs text-primary hover:text-primary"
                          disabled={scoringId === fav.id}
                          title="对该组合立即打分并回写收藏"
                          onClick={(e) => {
                            e.stopPropagation(); // 阻止触发卡片点击
                            void quickScore(fav);
                          }}
                        >
                          {scoringId === fav.id ? '打分中…' : '一键打分'}
                        </Button>
                      )}
                      <span className="truncate text-xs text-muted-foreground">
                        {fav.created_at || ''}
                      </span>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 详情 Dialog */}
      <FavoriteDetailDialog
        fav={detail}
        open={detail !== null}
        onOpenChange={(v) => !v && setDetail(null)}
        onFavUpdated={(updated) => {
          setDetail(updated);
          onChanged();
        }}
      />

      {/* 移动 / 复制收藏：选择目标收藏夹（排除当前所在夹） */}
      <FavoriteFolderDialog
        open={moveCopyTarget !== null}
        onOpenChange={(v) => !v && setMoveCopyTarget(null)}
        title={moveCopyTarget?.mode === 'copy' ? '复制收藏到…' : '移动收藏到…'}
        description={
          moveCopyTarget
            ? moveCopyTarget.mode === 'copy'
              ? `将「${moveCopyTarget.fav.aldehyde?.name || '未知醛'} × ${moveCopyTarget.fav.amine?.name || '未知胺'}」复制一份到所选收藏夹；实验记录归属不随复制转移。`
              : `将「${moveCopyTarget.fav.aldehyde?.name || '未知醛'} × ${moveCopyTarget.fav.amine?.name || '未知胺'}」移动到所选收藏夹。`
            : undefined
        }
        excludeFolderId={moveCopyTarget?.fav.folder_id}
        confirmLabel={moveCopyTarget?.mode === 'copy' ? '复制' : '移动'}
        submitting={moveCopying}
        onConfirm={handleMoveCopy}
      />

      {/* 删除收藏确认 */}
      <AlertDialog open={toDelete !== null} onOpenChange={(v) => !v && setToDelete(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除该收藏？</AlertDialogTitle>
            <AlertDialogDescription>
              将删除收藏「{toDelete?.aldehyde?.name || '未知醛'} × {toDelete?.amine?.name || '未知胺'}」
              （{toDelete?.id}），关联的实验记录不会被删除。此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={confirmDelete}
              disabled={deleting}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {deleting ? '删除中…' : '确认删除'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 新建收藏夹 */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="w-[calc(100vw-1.5rem)] sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>新建收藏夹</DialogTitle>
            <DialogDescription>收藏夹名称不可与现有收藏夹重复。</DialogDescription>
          </DialogHeader>
          <Input
            value={newFolderName}
            onChange={(e) => setNewFolderName(e.target.value)}
            placeholder="如 高分候选"
            maxLength={30}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void handleCreateFolder();
            }}
          />
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setCreateOpen(false)} disabled={creatingFolder}>
              取消
            </Button>
            <Button
              onClick={() => void handleCreateFolder()}
              disabled={creatingFolder || !newFolderName.trim()}
            >
              {creatingFolder ? '创建中…' : '创建'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* 收藏夹改名 */}
      <Dialog open={renameTarget !== null} onOpenChange={(v) => !v && setRenameTarget(null)}>
        <DialogContent className="w-[calc(100vw-1.5rem)] sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>收藏夹改名</DialogTitle>
            <DialogDescription>
              当前名称「{renameTarget?.name}」，新名称不可与其他收藏夹重复。
            </DialogDescription>
          </DialogHeader>
          <Input
            value={renameName}
            onChange={(e) => setRenameName(e.target.value)}
            maxLength={30}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void handleRenameFolder();
            }}
          />
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setRenameTarget(null)} disabled={renaming}>
              取消
            </Button>
            <Button
              onClick={() => void handleRenameFolder()}
              disabled={renaming || !renameName.trim()}
            >
              {renaming ? '保存中…' : '保存'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* 删除收藏夹：第一次确认（说明将删 N 条收藏） */}
      <AlertDialog
        open={folderDelete !== null && deleteStep === 1}
        onOpenChange={(v) => !v && setFolderDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除收藏夹「{folderDelete?.name}」？</AlertDialogTitle>
            <AlertDialogDescription>
              将删除该收藏夹及其中 {deleteCount} 条收藏（含打分/DFT 快照），删除后不可恢复。
              收藏关联的实验记录本身不会被删除。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            {/* 用普通 Button 避免 AlertDialogAction 自动关闭打断两步流程 */}
            <Button
              variant="destructive"
              onClick={() => setDeleteStep(2)}
            >
              继续
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 删除收藏夹：第二次确认（非空夹须输入名称；空夹再次点确认） */}
      <AlertDialog
        open={folderDelete !== null && deleteStep === 2}
        onOpenChange={(v) => {
          if (!v) {
            setDeleteStep(1);
            setDeleteConfirmText('');
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>再次确认：此操作不可恢复</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteCount > 0
                ? `请输入收藏夹名称「${folderDelete?.name}」以确认删除其中 ${deleteCount} 条收藏。`
                : '该收藏夹为空，再次确认后将立即删除，不可恢复。'}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {deleteCount > 0 && (
            <Input
              value={deleteConfirmText}
              onChange={(e) => setDeleteConfirmText(e.target.value)}
              placeholder={folderDelete?.name}
            />
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deletingFolder}>取消</AlertDialogCancel>
            <Button
              variant="destructive"
              onClick={() => void handleDeleteFolder()}
              disabled={
                deletingFolder || (deleteCount > 0 && deleteConfirmText.trim() !== folderDelete?.name)
              }
            >
              {deletingFolder ? '删除中…' : '确认删除'}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
