/**
 * DFT 计算页（DFT 2.0 · docs/DFT2.0设计方案.md §一/§三）
 * 计算对象：醛/胺单体 → 缩合二聚体 D，D 与第三物质 X 的结合能。
 * 三步布局：第一步 两个单体输入 + 二聚体预览（结构图 + SMILES + 多位点标注）；
 * 第二步 X 类型单选（自身堆积 / 溶剂下拉 / 另一组单体 / 自定义 SMILES）+ 方法档位；
 * 第三步 开始计算。异步任务轮询（1.5s），全程中文进度提示与失败原因。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { CircleHelp } from 'lucide-react';
import MonomerInput, { type MonomerValue } from '@/components/query/MonomerInput';
import StructureSketcher from '@/components/common/StructureSketcher';
import MonomerPropsCard from '@/components/query/MonomerPropsCard';
import FavoriteFolderDialog from '@/components/common/FavoriteFolderDialog';
import {
  DuplicateFavoriteError,
  fetchFavorites,
  fetchMonomerProps,
  fetchMonomers,
  type FavoriteItem,
  type MonomerLibrary,
  type MonomerProps,
} from '@/components/query/api';
import { appendDftEntry } from '@/components/mine/api';
import DftResultPanel from '@/components/dft/DftResultPanel';
import {
  buildDftSnapshot,
  createDftJob,
  createFavoriteWithDft,
  dftMethodLabel,
  fetchDftBackends,
  fetchDftHistory,
  fetchDftJob,
  fetchDftSolvents,
  fetchDimerPreview,
  mergeDftToFavorite,
  type DftBackend,
  type DftBackendsResponse,
  type DftHistoryEntry,
  type DftMethod,
  type DftMode,
  type DftResult,
  type DftSolvent,
  type DftXType,
  type DimerPreview,
} from '@/components/dft/api';

interface PropsState {
  loading: boolean;
  error: string | null;
  data: MonomerProps | null;
}
const emptyProps: PropsState = { loading: false, error: null, data: null };

/**
 * 粗估 SMILES 重原子数（数元素符号：双字母 Br/Cl、大写开头符号、芳香小写
 * c/n/o/s/p）。不求精确，仅供大体系长时提示的阈值判断。
 */
function estimateHeavyAtoms(smiles: string): number {
  if (!smiles) return 0;
  const m = smiles.match(/Br|Cl|[A-Z][a-z]?|[cnosp]/g);
  return m ? m.length : 0;
}

/** 历史条目回显 → 拼装成 DftResult（旧条目缺二聚体/X 字段时留空兜底） */
function resultFromHistory(h: DftHistoryEntry): DftResult {
  return {
    mode: h.mode ?? 'dimer',
    backend: h.backend ?? 'xtb',
    smiles_a: h.smiles_a,
    smiles_b: h.smiles_b,
    dimer_smiles: h.dimer_smiles ?? '',
    dimer_multi_site: h.dimer_multi_site ?? false,
    dimer_note: h.dimer_note ?? null,
    x_type: h.x_type ?? 'self_stack',
    x_smiles: h.x_smiles ?? '',
    x_description: h.x_description ?? '（旧记录未保存 X 描述）',
    x_request: h.x_request,
    method: h.method,
    method_label: dftMethodLabel(h.backend, h.method, h.method_label),
    e_bind_hartree: 0,
    e_bind_kcal: h.e_bind_kcal ?? 0,
    e_bind_kj: h.e_bind_kj ?? 0,
    energies_hartree: h.energies_hartree ?? { dimer: 0, x: 0, complex: 0 },
    gap_ev: h.gap_ev ?? { dimer: null, x: null, complex: null },
    dipole_debye: h.dipole_debye ?? { dimer: null, x: null, complex: null },
    complex_xyz: h.complex_xyz ?? '',
    fragment_ranges: h.fragment_ranges ?? null,
    elapsed_sec: h.elapsed_sec ?? 0,
    cached: true,
    favorite: null,
  };
}

const X_TYPE_OPTIONS: { value: DftXType; label: string; hint: string }[] = [
  { value: 'self_stack', label: '自身堆积（二聚体·二聚体）', hint: 'π-π 堆积 / 自聚集倾向，结晶与成膜驱动力' },
  { value: 'solvent', label: '溶剂分子', hint: '二聚体在溶剂中的分散倾向，成膜环境筛选' },
  { value: 'other_dimer', label: '另一组单体形成的二聚体', hint: '异质堆积（供体-受体对）' },
  { value: 'custom', label: '自定义分子', hint: '灵活探索（如基底模型物、调节剂）' },
];

export default function Dft() {
  // ---------- 计算模式：dimer（醛胺缩合二聚体·X）| pair（任意双分子 A···B） ----------
  const [mode, setMode] = useState<DftMode>('dimer');
  const isPair = mode === 'pair';

  // ---------- 第一步：二聚体 ----------
  const [monoA, setMonoA] = useState<MonomerValue>({ smiles: '', name: '' });
  const [monoB, setMonoB] = useState<MonomerValue>({ smiles: '', name: '' });
  const [dimerPreview, setDimerPreview] = useState<DimerPreview | null>(null);
  const [dimerError, setDimerError] = useState<string | null>(null);
  const [dimerLoading, setDimerLoading] = useState(false);

  // ---------- 第二步：X 类型 ----------
  const [xType, setXType] = useState<DftXType>('self_stack');
  const [solvents, setSolvents] = useState<DftSolvent[]>([]);
  const [solventId, setSolventId] = useState<string>('');
  const [monoA2, setMonoA2] = useState<MonomerValue>({ smiles: '', name: '' });
  const [monoB2, setMonoB2] = useState<MonomerValue>({ smiles: '', name: '' });
  const [customSmiles, setCustomSmiles] = useState('');
  const [method, setMethod] = useState<DftMethod>('gfn2');

  // ---------- 计算后端：xtb 快速档（默认）| psi4 真 DFT 精度档 ----------
  const [backend, setBackend] = useState<DftBackend>('xtb');
  /** Psi4 方法 preset：wb97xd3bj_svp（高精度泛函）| b3lyp_631gdp（文献口径） */
  const [psi4Method, setPsi4Method] = useState<DftMethod>('wb97xd3bj_svp');
  const isPsi4 = backend === 'psi4';
  /** GET /api/dft/backends 的可用状态（null = 尚未取到） */
  const [backends, setBackends] = useState<DftBackendsResponse['backends'] | null>(null);
  const psi4Installed = backends?.psi4?.installed === true;

  /** 大体系长时提示：粗估复合物总原子数（重原子×2 近似含氢），>50 时提示 */
  const estTotalAtoms = (() => {
    const dimerHeavy = estimateHeavyAtoms(monoA.smiles) + estimateHeavyAtoms(monoB.smiles);
    if (!dimerHeavy) return 0;
    let heavy = dimerHeavy;
    if (!isPair) {
      if (xType === 'self_stack') heavy = dimerHeavy * 2;
      else if (xType === 'other_dimer')
        heavy = dimerHeavy + estimateHeavyAtoms(monoA2.smiles) + estimateHeavyAtoms(monoB2.smiles);
      else if (xType === 'custom') heavy = dimerHeavy + estimateHeavyAtoms(customSmiles);
      else heavy = dimerHeavy + 10; // 溶剂小分子粗估
    }
    return heavy * 2;
  })();

  /** URL 预填（收藏详情「重新计算」跳转）：?a=<smiles>&b=<smiles>&an=<名>&bn=<名> */
  const [searchParams] = useSearchParams();

  // ---------- 后端状态 ----------
  const [backendDown, setBackendDown] = useState(false);
  const [library, setLibrary] = useState<MonomerLibrary>({ aldehydes: [], amines: [] });
  const [libraryLoading, setLibraryLoading] = useState(true);

  // ---------- 任务状态 ----------
  const [progressHint, setProgressHint] = useState('');
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<DftResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  /** 当前结果对应的任务 id（导出输入文件用；历史回显时为 null） */
  const [currentJobId, setCurrentJobId] = useState<string | null>(null);

  // ---------- 联动 ----------
  const [aProps, setAProps] = useState<PropsState>(emptyProps);
  const [bProps, setBProps] = useState<PropsState>(emptyProps);
  const [history, setHistory] = useState<DftHistoryEntry[]>([]);
  const [favoriting, setFavoriting] = useState(false);
  /** 收藏前选择目标收藏夹的对话框 */
  const [favDialogOpen, setFavDialogOpen] = useState(false);
  /** 当前结果双序匹配的已收藏组合（前端查 favorites 兜底 result.favorite） */
  const [pairFavorite, setPairFavorite] = useState<FavoriteItem | null>(null);
  /** 追加 DFT 条目进行中 */
  const [appending, setAppending] = useState(false);
  /** 409 冲突时待合并的已有收藏摘要 */
  const [mergeTarget, setMergeTarget] = useState<{ id: string; folder_name?: string; aldehyde_name?: string; amine_name?: string; has_dft?: boolean } | null>(null);
  const [merging, setMerging] = useState(false);

  const refreshHistory = useCallback(() => {
    fetchDftHistory().then(setHistory).catch(() => {});
  }, []);

  useEffect(() => {
    // URL 预填单体（收藏详情「重新计算 / 继续计算其他物质」跳转）
    const preA = searchParams.get('a');
    const preB = searchParams.get('b');
    if (preA) setMonoA({ smiles: preA, name: searchParams.get('an') ?? '' });
    if (preB) setMonoB({ smiles: preB, name: searchParams.get('bn') ?? '' });
    // 从收藏跳转时提示已预填（X 类型保持默认「自身堆积」，可切换自定义）
    if (searchParams.get('from') === 'favorite' && (preA || preB)) {
      toast.info('已预填该收藏组合的醛/胺单体，可在第二步选择其他 X 物质继续计算');
    }
    fetchMonomers()
      .then((lib) => { setLibrary(lib); setBackendDown(false); })
      .catch(() => { setLibrary({ aldehydes: [], amines: [] }); setBackendDown(true); })
      .finally(() => setLibraryLoading(false));
    fetchDftSolvents()
      .then((list) => { setSolvents(list); if (list.length > 0) setSolventId(list[0].id); })
      .catch(() => {});
    fetchDftBackends().then(setBackends).catch(() => {});
    refreshHistory();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 选中 Psi4 但未安装时：每 15s 轮询检测（用户可能在跑 install_psi4_env.bat）
  useEffect(() => {
    if (!isPsi4 || psi4Installed) return;
    const timer = setInterval(() => {
      fetchDftBackends()
        .then((b) => {
          setBackends(b);
          if (b.psi4?.installed) toast.success(`已检测到 Psi4 精度档环境（v${b.psi4.version ?? '?'}）`);
        })
        .catch(() => {});
    }, 15000);
    return () => clearInterval(timer);
  }, [isPsi4, psi4Installed]);

  // ---------- 二聚体预览（两个单体 SMILES 齐备后防抖请求；pair 模式跳过） ----------
  useEffect(() => {
    if (mode === 'pair' || !monoA.smiles || !monoB.smiles) {
      setDimerPreview(null);
      setDimerError(null);
      setDimerLoading(false);
      return;
    }
    setDimerLoading(true);
    const timer = setTimeout(() => {
      fetchDimerPreview(monoA.smiles, monoB.smiles)
        .then((p) => { setDimerPreview(p); setDimerError(null); })
        .catch((e) => {
          setDimerPreview(null);
          setDimerError(e instanceof Error ? e.message : '二聚体预览失败');
        })
        .finally(() => setDimerLoading(false));
    }, 500);
    return () => clearTimeout(timer);
  }, [mode, monoA.smiles, monoB.smiles]);

  /** 轮询任务直至 done/failed */
  const startPolling = useCallback((id: string) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const job = await fetchDftJob(id);
        setProgressHint(job.progress_hint);
        if (job.status === 'done' && job.result) {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          setRunning(false);
          setResult(job.result);
          toast.success(job.cached ? '命中缓存，已返回历史结果' : '计算完成');
          refreshHistory();
        } else if (job.status === 'failed') {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          setRunning(false);
          setError(job.error || '计算失败（未知原因）');
          refreshHistory();
        }
      } catch {
        // 轮询失败（后端重启等）：停止轮询并提示
        if (pollRef.current) clearInterval(pollRef.current);
        pollRef.current = null;
        setRunning(false);
        setError('任务状态查询失败：后端可能已重启，任务结果未保留，请重新提交');
      }
    }, 1500);
  }, [refreshHistory]);

  /** 提交计算 */
  const handleSubmit = async () => {
    if (!monoA.smiles || !monoB.smiles) {
      toast.warning(isPair ? '请先填写分子 A 与分子 B 的 SMILES' : '请先填写醛单体与胺单体的 SMILES');
      return;
    }
    if (isPsi4 && !psi4Installed) {
      toast.warning('Psi4 精度档环境未安装，请先按引导完成安装');
      return;
    }
    if (!isPair && xType === 'solvent' && !solventId) {
      toast.warning('请选择溶剂');
      return;
    }
    if (!isPair && xType === 'other_dimer' && (!monoA2.smiles || !monoB2.smiles)) {
      toast.warning('请填写另一组醛/胺单体的 SMILES');
      return;
    }
    if (!isPair && xType === 'custom' && !customSmiles.trim()) {
      toast.warning('请输入自定义分子的 SMILES');
      return;
    }
    setRunning(true);
    setResult(null);
    setError(null);
    setAProps(emptyProps);
    setBProps(emptyProps);
    setProgressHint('正在提交计算任务…');
    try {
      const job = await createDftJob(
        isPair
          ? {
            mode: 'pair',
            ald_smiles: monoA.smiles,
            amine_smiles: monoB.smiles,
            method: isPsi4 ? psi4Method : method,
            backend,
          }
          : {
            ald_smiles: monoA.smiles,
            amine_smiles: monoB.smiles,
            x_type: xType,
            solvent_id: xType === 'solvent' ? solventId : undefined,
            ald2_smiles: xType === 'other_dimer' ? monoA2.smiles : undefined,
            amine2_smiles: xType === 'other_dimer' ? monoB2.smiles : undefined,
            custom_smiles: xType === 'custom' ? customSmiles.trim() : undefined,
            method: isPsi4 ? psi4Method : method,
            backend,
          },
      );
      setCurrentJobId(job.job_id);
      if (job.status === 'done' && job.result) {
        // 缓存命中：无需轮询
        setRunning(false);
        setResult(job.result);
        setProgressHint('');
        toast.success('命中缓存，已返回历史结果');
      } else {
        setProgressHint(job.progress_hint);
        startPolling(job.job_id);
      }
    } catch {
      setRunning(false);
      setProgressHint('');
      // toast 已在 api 辅助中弹出
    }
  };

  /** 加载两侧性质卡 */
  const loadProps = useCallback((smiles: string, name: string, setter: React.Dispatch<React.SetStateAction<PropsState>>) => {
    setter({ loading: true, error: null, data: null });
    fetchMonomerProps(smiles, name)
      .then((data) => setter({ loading: false, error: null, data }))
      .catch((e) => setter({ loading: false, error: e instanceof Error ? e.message : '未知错误', data: null }));
  }, []);

  // 结果就绪后联动性质卡（醛/胺单体）
  useEffect(() => {
    if (result) {
      loadProps(result.smiles_a, monoA.name, setAProps);
      loadProps(result.smiles_b, monoB.name, setBProps);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result]);

  /** 收藏：先弹收藏夹选择对话框，确认后带 folder_id 创建（含 DFT 快照；409 时弹合并对话框） */
  const handleFavorite = () => {
    if (!result) return;
    setFavDialogOpen(true);
  };

  /** 确认目标收藏夹后创建收藏 */
  const handleConfirmFavorite = async (folderId: string, folderName: string) => {
    if (!result) return;
    setFavoriting(true);
    try {
      await createFavoriteWithDft({
        aldehyde_smiles: result.smiles_a,
        amine_smiles: result.smiles_b,
        ald_name: monoA.name,
        amine_name: monoB.name,
        folder_id: folderId,
        dft_snapshot: buildDftSnapshot(result),
      });
      toast.success(`已收藏到「${folderName || '收藏夹1'}」（含 DFT 结果）`);
      setFavDialogOpen(false);
      setResult({ ...result, favorite: { id: '', has_dft: true } });
    } catch (e) {
      setFavDialogOpen(false);
      if (e instanceof DuplicateFavoriteError) {
        setMergeTarget(e.existing);
      } else {
        toast.error(`收藏失败：${e instanceof Error ? e.message : '未知错误'}`);
      }
    } finally {
      setFavoriting(false);
    }
  };

  // 结果就绪后：双序查配对（前端查 favorites 找匹配，兜底 result.favorite）
  useEffect(() => {
    if (!result?.smiles_a || !result?.smiles_b) {
      setPairFavorite(null);
      return;
    }
    if (result.favorite?.id) {
      setPairFavorite(null);
      return;
    }
    let cancelled = false;
    fetchFavorites()
      .then((list) => {
        if (cancelled) return;
        const hit = list.find(
          (f) =>
            (f.aldehyde?.smiles === result.smiles_a && f.amine?.smiles === result.smiles_b) ||
            (f.aldehyde?.smiles === result.smiles_b && f.amine?.smiles === result.smiles_a),
        );
        setPairFavorite(hit ?? null);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [result]);

  /** 追加本次 DFT 结果到已收藏组合的 dft_entries */
  const handleAppendDftEntry = async () => {
    if (!result) return;
    const targetId = result.favorite?.id || pairFavorite?.id;
    if (!targetId) {
      toast.warning('未找到该组合对应的收藏');
      return;
    }
    setAppending(true);
    try {
      await appendDftEntry(targetId, {
        job_id: currentJobId ?? undefined,
        x_type: result.x_type ?? undefined,
        x_smiles: result.x_smiles,
        x_description: result.x_description,
        dimer_smiles: result.dimer_smiles ?? undefined,
        method: result.method,
        backend: result.backend ?? 'xtb',
        e_bind_kcal: result.e_bind_kcal,
        e_bind_kj: result.e_bind_kj,
        created_at: new Date().toISOString(),
      });
      toast.success('已追加到收藏的 DFT 记录');
      setResult({ ...result, favorite: { ...(result.favorite ?? { id: targetId }), id: targetId, has_dft: true } });
    } catch {
      /* 错误提示已由 api 层弹出 */
    } finally {
      setAppending(false);
    }
  };

  /** 合并 DFT 结果到已有收藏（PATCH dft_snapshot） */
  const handleMerge = async () => {
    const targetId = mergeTarget?.id || result?.favorite?.id;
    if (!targetId || !result) {
      toast.warning('无法定位已有收藏，请到「我的」页手动操作');
      return;
    }
    setMerging(true);
    try {
      await mergeDftToFavorite(targetId, result);
      toast.success('DFT 结果已合并到已有收藏');
      setMergeTarget(null);
      setResult({ ...result, favorite: { ...(result.favorite ?? { id: targetId }), id: targetId, has_dft: true } });
    } catch {
      // toast 已弹
    } finally {
      setMerging(false);
    }
  };

  /** 历史点击：回显输入与结果（无任务 id，导出时会借缓存命中任务） */
  const handleHistoryClick = (h: DftHistoryEntry) => {
    setMode(h.mode ?? 'dimer');
    setBackend(h.backend ?? 'xtb');
    setMonoA({ smiles: h.smiles_a, name: '' });
    setMonoB({ smiles: h.smiles_b, name: '' });
    if (h.method === 'gfnff' || h.method === 'gfn2') setMethod(h.method);
    if (h.method === 'wb97xd3bj_svp' || h.method === 'b3lyp_631gdp') setPsi4Method(h.method);
    if (h.x_type) setXType(h.x_type);
    if (h.x_request?.solvent_id) setSolventId(h.x_request.solvent_id);
    if (h.x_request?.ald2_smiles) setMonoA2({ smiles: h.x_request.ald2_smiles, name: '' });
    if (h.x_request?.amine2_smiles) setMonoB2({ smiles: h.x_request.amine2_smiles, name: '' });
    if (h.x_request?.custom_smiles) setCustomSmiles(h.x_request.custom_smiles);
    setError(h.status === 'failed' ? (h.error || '计算失败') : null);
    setResult(h.status === 'done' ? resultFromHistory(h) : null);
    setCurrentJobId(null);
    setRunning(false);
  };

  const fav = result?.favorite;
  /** 追加 DFT 记录的目标收藏 id（后端联动 result.favorite 或前端双序匹配） */
  const appendTargetId = fav?.id || pairFavorite?.id;

  /** 同组合另一后端的最近一次成功结果（精度档/快速档并排对比用） */
  const compareResult = (() => {
    if (!result || history.length === 0) return null;
    const curBackend = result.backend ?? 'xtb';
    const peer = history.find(
      (h) =>
        h.status === 'done' &&
        h.e_bind_kcal != null &&
        (h.backend ?? 'xtb') !== curBackend &&
        (h.mode ?? 'dimer') === (result.mode ?? 'dimer') &&
        (h.dimer_smiles ?? h.smiles_a) === (result.dimer_smiles || result.smiles_a) &&
        (h.x_smiles ?? h.smiles_b) === (result.x_smiles || result.smiles_b),
    );
    if (!peer || peer.e_bind_kcal == null) return null;
    const peerKcal: number = peer.e_bind_kcal;
    return {
      backend: (peer.backend ?? 'xtb') as DftBackend,
      method_label: dftMethodLabel(peer.backend, peer.method, peer.method_label),
      e_bind_kcal: peerKcal,
      e_bind_kj: peer.e_bind_kj ?? peerKcal * 4.184,
    };
  })();

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold text-gradient-royal">DFT 计算</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          计算「缩合二聚体与第三物质」的结合能，辅助判断堆积 / 溶剂化 / 异质聚集倾向；
          支持 xTB 半经验快速档与 Psi4 真 DFT 精度档（BSSE 校正）
        </p>
      </div>

      {backendDown && (
        <Alert className="border-yellow-300 bg-yellow-50 dark:border-yellow-900 dark:bg-yellow-950/40">
          <AlertTitle className="text-yellow-800 dark:text-yellow-300">后端未连接</AlertTitle>
          <AlertDescription className="text-yellow-700 dark:text-yellow-400">
            无法连接 FastAPI 服务。DFT 计算、单体库与历史记录暂不可用，请启动后端后刷新页面。
          </AlertDescription>
        </Alert>
      )}

      {/* 计算模式切换：二聚体模式 / 任意双分子模式 */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {([
          {
            value: 'dimer' as DftMode,
            title: '二聚体模式（COF 醛胺缩合）',
            desc: '醛 + 胺 → 亚胺缩合二聚体，再与第三物质 X（自身堆积 / 溶剂 / 异质二聚体 / 自定义）计算结合能',
          },
          {
            value: 'pair' as DftMode,
            title: '任意双分子模式',
            desc: '直接输入任意两个分子 A 和 B，计算 A···B 复合物结合能，不经过二聚体生成、不限醛胺体系',
          },
        ]).map((opt) => (
          <button
            key={opt.value}
            type="button"
            disabled={running}
            onClick={() => setMode(opt.value)}
            className={`rounded-xl border p-4 text-left transition-colors ${
              mode === opt.value
                ? 'border-gold bg-gold-muted/40 ring-1 ring-gold'
                : 'bg-card hover:bg-accent'
            } ${running ? 'cursor-not-allowed opacity-60' : ''}`}
          >
            <p className="text-sm font-semibold text-foreground">{opt.title}</p>
            <p className="mt-1 text-xs text-muted-foreground">{opt.desc}</p>
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* 左侧输入区 */}
        <div className="space-y-4">
          {/* 第一步：二聚体 / 双分子 */}
          <div className="space-y-3">
            <h2 className="text-sm font-semibold text-foreground">
              {isPair ? '第一步：两个分子（任意体系）' : '第一步：二聚体（醛 + 胺 → 亚胺缩合）'}
            </h2>
            <MonomerInput
              title={isPair ? '分子 A' : '醛单体'}
              role="aldehyde"
              value={monoA}
              onChange={setMonoA}
              library={isPair ? [...library.aldehydes, ...library.amines] : library.aldehydes}
              libraryLoading={libraryLoading}
              disabled={running}
            />
            <MonomerInput
              title={isPair ? '分子 B' : '胺单体'}
              role="amine"
              value={monoB}
              onChange={setMonoB}
              library={isPair ? [...library.aldehydes, ...library.amines] : library.amines}
              libraryLoading={libraryLoading}
              disabled={running}
            />

            {/* 二聚体预览（仅二聚体模式） */}
            {!isPair && monoA.smiles && monoB.smiles && (
              <div className="rounded-xl border bg-card p-4">
                <h3 className="mb-2 text-sm font-semibold text-foreground">二聚体预览</h3>
                {dimerLoading && <p className="text-xs text-muted-foreground">正在生成缩合二聚体预览…</p>}
                {!dimerLoading && dimerError && (
                  <p className="text-xs text-red-700 dark:text-red-400">{dimerError}</p>
                )}
                {!dimerLoading && dimerPreview && (
                  <div className="space-y-2">
                    <img
                      src={`/api/monomers/structure.svg?smiles=${encodeURIComponent(dimerPreview.dimer_smiles)}&w=600&h=300`}
                      alt="缩合二聚体结构图"
                      className="mx-auto max-h-40 rounded border bg-white object-contain p-1"
                      onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                    />
                    <code className="block break-all rounded border bg-muted/50 px-2 py-1 font-mono text-xs">
                      {dimerPreview.dimer_smiles}
                    </code>
                    {dimerPreview.multi_site && dimerPreview.note && (
                      <p className="text-xs text-amber-700 dark:text-amber-400">⚠️ {dimerPreview.note}</p>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* 第二步：与什么计算结合能（仅二聚体模式；pair 模式无 X 概念） */}
          {!isPair && (
          <div className="space-y-3 rounded-xl border bg-card p-4">
            <h2 className="text-sm font-semibold text-foreground">第二步：与什么计算结合能（X）</h2>
            <RadioGroup value={xType} onValueChange={(v) => setXType(v as DftXType)} disabled={running}>
              {X_TYPE_OPTIONS.map((opt) => (
                <div key={opt.value} className="space-y-1">
                  <div className="flex items-center space-x-2">
                    <RadioGroupItem value={opt.value} id={`x-${opt.value}`} />
                    <Label htmlFor={`x-${opt.value}`}>{opt.label}</Label>
                  </div>
                  {xType === opt.value && (
                    <p className="pl-6 text-xs text-muted-foreground">{opt.hint}</p>
                  )}
                </div>
              ))}
            </RadioGroup>

            {xType === 'solvent' && (
              <div className="space-y-1.5 pl-6 pt-1">
                <Label>选择溶剂</Label>
                <Select value={solventId} onValueChange={setSolventId} disabled={running || solvents.length === 0}>
                  <SelectTrigger>
                    <SelectValue placeholder={solvents.length === 0 ? '溶剂表加载中（后端未连接？）' : '选择溶剂'} />
                  </SelectTrigger>
                  <SelectContent>
                    {solvents.map((s) => (
                      <SelectItem key={s.id} value={s.id}>
                        {s.name_zh}（{s.smiles}）
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}

            {xType === 'other_dimer' && (
              <div className="space-y-3 pl-6 pt-1">
                <MonomerInput
                  title="醛单体 2"
                  role="aldehyde"
                  value={monoA2}
                  onChange={setMonoA2}
                  library={library.aldehydes}
                  libraryLoading={libraryLoading}
                  disabled={running}
                />
                <MonomerInput
                  title="胺单体 2"
                  role="amine"
                  value={monoB2}
                  onChange={setMonoB2}
                  library={library.amines}
                  libraryLoading={libraryLoading}
                  disabled={running}
                />
              </div>
            )}

            {xType === 'custom' && (
              <div className="space-y-1.5 pl-6 pt-1">
                <Label>自定义分子 SMILES</Label>
                <div className="flex gap-2">
                  <Input
                    placeholder="如 CCO（乙醇）"
                    value={customSmiles}
                    disabled={running}
                    onChange={(e) => setCustomSmiles(e.target.value)}
                  />
                  {/* 画结构：无 CAS/不熟悉 SMILES 时用画板绘制，确定后回填 */}
                  <StructureSketcher
                    value={customSmiles}
                    disabled={running}
                    title="绘制自定义分子（X）结构"
                    onChange={setCustomSmiles}
                  />
                </div>
              </div>
            )}
          </div>
          )}

          {/* 计算后端：xTB 快速档 / Psi4 精度档 */}
          <div className="space-y-2 rounded-xl border bg-card p-4">
            <div className="flex items-center gap-1.5">
              <h3 className="font-semibold text-foreground">计算后端</h3>
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <CircleHelp className="h-4 w-4 cursor-help text-muted-foreground" />
                  </TooltipTrigger>
                  <TooltipContent className="max-w-xs text-xs">
                    xTB 快速档：半经验 GFN2-xTB/GFN-FF，秒级出结果，适合批量筛选与相对比较；
                    Psi4 精度档：真 DFT（ωB97X-D3BJ/def2-SVP 或 B3LYP/6-31G(d,p) 文献口径），
                    结合能做 BSSE counterpoise 校正，分钟级耗时，结果带 fchk 文件可对接 Gaussian 工作流。
                    两档默认均经 Monte Carlo 多取向采样 + xTB 筛选确定复合物几何。
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            </div>
            <RadioGroup value={backend} onValueChange={(v) => setBackend(v as DftBackend)} disabled={running}>
              <div className="flex items-center space-x-2">
                <RadioGroupItem value="xtb" id="b-xtb" />
                <Label htmlFor="b-xtb">xTB 快速档（秒级，批量筛选）</Label>
              </div>
              <div className="flex items-center space-x-2">
                <RadioGroupItem value="psi4" id="b-psi4" />
                <Label htmlFor="b-psi4">
                  Psi4 精确（真 DFT，分钟级）
                  {psi4Installed && backends?.psi4?.version && (
                    <span className="ml-1 text-xs text-muted-foreground">v{backends.psi4.version}</span>
                  )}
                </Label>
              </div>
            </RadioGroup>

            {/* Psi4 未安装：引导安装卡 */}
            {isPsi4 && backends && !psi4Installed && (
              <Alert className="border-amber-300 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/40">
                <AlertTitle className="text-amber-800 dark:text-amber-300">Psi4 精度档未安装</AlertTitle>
                <AlertDescription className="space-y-2 text-amber-700 dark:text-amber-400">
                  <p>
                    {backends.psi4?.install_hint ??
                      '请运行 scripts/install_psi4_env.bat 一键安装（conda create -n psi4-env -c conda-forge psi4 python=3.11，约 300MB+ 下载）。'}
                  </p>
                  <p className="text-xs">
                    安装脚本运行期间本页面每 15 秒自动检测一次；装好后无需刷新即可使用。
                  </p>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => fetchDftBackends().then(setBackends).catch(() => {})}
                  >
                    立即重新检测
                  </Button>
                </AlertDescription>
              </Alert>
            )}
            {isPsi4 && backends === null && (
              <p className="text-xs text-muted-foreground">正在检测 Psi4 环境…</p>
            )}
          </div>

          {/* 方法档位（仅 xTB 后端；Psi4 固定 ωB97X-D3BJ/def2-SVP） */}
          {!isPsi4 && (
          <div className="space-y-2 rounded-xl border bg-card p-4">
            <div className="flex items-center gap-1.5">
              <h3 className="font-semibold text-foreground">方法档位</h3>
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <CircleHelp className="h-4 w-4 cursor-help text-muted-foreground" />
                  </TooltipTrigger>
                  <TooltipContent className="max-w-xs text-xs">
                    快速（GFN-FF 力场）：秒级出结果，适合粗筛相对比较；
                    精确（GFN2-xTB 半经验）：通常数十秒，二聚体·二聚体等大体系可能数分钟，
                    能量与电子结构更可靠。两者均为半经验方法，精确能量请导出几何用 DFT 复算。
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            </div>
            <RadioGroup value={method} onValueChange={(v) => setMethod(v as DftMethod)} disabled={running}>
              <div className="flex items-center space-x-2">
                <RadioGroupItem value="gfn2" id="m-gfn2" />
                <Label htmlFor="m-gfn2">精确（GFN2-xTB，推荐）</Label>
              </div>
              <div className="flex items-center space-x-2">
                <RadioGroupItem value="gfnff" id="m-gfnff" />
                <Label htmlFor="m-gfnff">快速（GFN-FF 力场）</Label>
              </div>
            </RadioGroup>
          </div>
          )}

          {/* Psi4 方法 preset（高精度泛函 / 文献口径） */}
          {isPsi4 && (
            <div className="space-y-2 rounded-xl border bg-card p-4">
              <div className="flex items-center gap-1.5">
                <h3 className="font-semibold text-foreground">方法 / 基组</h3>
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <CircleHelp className="h-4 w-4 cursor-help text-muted-foreground" />
                    </TooltipTrigger>
                    <TooltipContent className="max-w-xs text-xs">
                      高精度泛函（默认）：ωB97X-D3BJ/def2-SVP，色散校正完备，S66 基准实测误差
                      0.1–0.2 kcal/mol（取向命中时）；文献口径：B3LYP/6-31G(d,p)，对齐刘璐 2021
                      等 COF 吸附文献的方法学，便于直接对比发表值（无色散校正，绝对值仅供参考）。
                      两者结合能均经 counterpoise（BSSE）校正。
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </div>
              <RadioGroup value={psi4Method} onValueChange={(v) => setPsi4Method(v as DftMethod)} disabled={running}>
                <div className="flex items-center space-x-2">
                  <RadioGroupItem value="wb97xd3bj_svp" id="pm-precision" />
                  <Label htmlFor="pm-precision">高精度泛函（ωB97X-D3BJ/def2-SVP，推荐）</Label>
                </div>
                <div className="flex items-center space-x-2">
                  <RadioGroupItem value="b3lyp_631gdp" id="pm-literature" />
                  <Label htmlFor="pm-literature">文献口径（B3LYP/6-31G(d,p)，对齐已发表 COF 吸附计算）</Label>
                </div>
              </RadioGroup>
              <p className="text-xs text-muted-foreground">
                结合能经 counterpoise（BSSE）校正；复合物几何经 Monte Carlo 多取向采样 + xTB 筛选
                再以 xTB 预优化。输出含 HOMO-LUMO 能隙、偶极矩与 fchk 检查点文件。
              </p>
            </div>
          )}

          {/* 第三步：开始计算 */}
          {isPsi4 && estTotalAtoms > 50 && (
            <p className="text-xs text-amber-600 dark:text-amber-400">
              ⏳ 当前组合预估复合物约 {estTotalAtoms} 个原子（&gt;50），Psi4 精度档可能需要 30 分钟以上，请耐心等待；计算在后台进行，期间可切换其他页面。
            </p>
          )}
          <Button
            className="w-full"
            size="lg"
            onClick={handleSubmit}
            disabled={running || !monoA.smiles || !monoB.smiles || (isPsi4 && !psi4Installed)}
            title={isPsi4 && !psi4Installed ? 'Psi4 精度档环境未安装，请先按上方引导完成安装' : undefined}
          >
            {running ? '计算中…' : isPsi4 && !psi4Installed ? '开始计算（需先安装 Psi4 精度档）' : '开始计算'}
          </Button>

          {/* 历史计算记录 */}
          {history.length > 0 && (
            <div className="rounded-lg border bg-card p-3 text-card-foreground shadow-sm">
              <h3 className="mb-2 text-sm font-medium">历史计算记录</h3>
              <ul className="max-h-72 space-y-1 overflow-y-auto text-sm">
                {history.map((h, i) => (
                  <li key={`${h.timestamp ?? ''}-${i}`}>
                    <button
                      type="button"
                      onClick={() => handleHistoryClick(h)}
                      className="w-full rounded px-2 py-1 text-left hover:bg-accent"
                      title={`${h.smiles_a} + ${h.smiles_b}${h.x_description ? `｜X：${h.x_description}` : ''}`}
                    >
                      <span className="mr-2 text-xs text-muted-foreground">
                        {(h.timestamp ?? '').replace('T', ' ').slice(0, 19)}
                      </span>
                      <Badge variant="outline" className="mr-1 text-[10px]">
                        {h.backend === 'psi4'
                          ? '真DFT'
                          : h.method === 'gfnff' ? '快速' : '精确'}
                      </Badge>
                      {h.backend === 'psi4' && (
                        <Badge className="mr-1 border-gold bg-gold-muted/60 text-[10px] text-amber-800 dark:text-gold" title="Psi4 精度档（ωB97X-D3BJ/def2-SVP，BSSE 校正）">
                          Psi4
                        </Badge>
                      )}
                      {h.mode === 'pair' && (
                        <Badge variant="secondary" className="mr-1 text-[10px]" title="任意双分子模式（A···B 直接结合）">
                          双分子
                        </Badge>
                      )}
                      <span className="font-mono text-xs">
                        {(h.dimer_smiles || h.smiles_a).slice(0, 12)}…
                      </span>
                      {h.x_description ? (
                        <span className="ml-1 text-xs text-muted-foreground">
                          {h.x_description}
                        </span>
                      ) : (
                        <Badge
                          variant="outline"
                          className="ml-1 border-amber-300 text-[10px] text-amber-700 dark:border-amber-800 dark:text-amber-400"
                          title="DFT 2.0 前的历史记录，未保存 X 描述（两单体结合能口径）"
                        >
                          旧版记录
                        </Badge>
                      )}
                      <span className="float-right font-medium tabular-nums">
                        {h.status === 'done' && h.e_bind_kcal != null
                          ? `${(typeof h.e_bind_kj === 'number' ? h.e_bind_kj : h.e_bind_kcal * 4.184).toFixed(1)} kJ/mol`
                          : '失败'}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {/* 右侧结果区 */}
        <div className="space-y-4 lg:col-span-2">
          {/* 进行中状态卡 */}
          {running && (
            <div className="rounded-lg border bg-card p-6 text-center shadow-sm">
              <div className="mx-auto mb-3 h-6 w-6 animate-spin rounded-full border-2 border-gold border-t-transparent" />
              <p className="text-sm font-medium">{progressHint || '计算中…'}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {isPsi4
                  ? 'Psi4 真 DFT 精度档通常需要数分钟（大体系可能更久），期间可离开本页，完成后从历史记录查看'
                  : method === 'gfn2'
                    ? '精确档位通常需要数十秒，二聚体·二聚体等大体系可能数分钟'
                    : '快速档位通常数秒内完成'}
              </p>
            </div>
          )}

          {/* 失败原因（中文） */}
          {error && !running && (
            <Alert className="border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950/40">
              <AlertTitle className="text-red-800 dark:text-red-300">计算失败</AlertTitle>
              <AlertDescription className="text-red-700 dark:text-red-400">{error}</AlertDescription>
            </Alert>
          )}

          {/* 空态 */}
          {!running && !result && !error && (
            <div className="rounded-lg border border-dashed bg-card/50 p-10 text-center text-sm text-muted-foreground">
              {isPair
                ? '输入任意两个分子 A 和 B 的 SMILES（或用画板绘制 / CAS 解析），选择方法档位后点击「开始计算」；结果包含 A···B 结合能、能隙、偶极矩与优化后复合物几何。'
                : '第一步输入醛/胺单体并确认二聚体预览，第二步选择与什么计算结合能，然后点击「开始计算」；结果包含结合能、能隙、偶极矩、二聚体 SMILES 与优化后复合物几何。'}
            </div>
          )}

          {/* 结果 */}
          {result && !running && (
            <>
              <DftResultPanel result={result} jobId={currentJobId} compare={compareResult} />

              {/* 收藏联动（pair 模式无单体组归属，禁用） */}
              {result.mode === 'pair' ? (
                <div className="flex flex-wrap items-center gap-2">
                  <Button variant="outline" disabled title="任意双分子模式暂不支持收藏">
                    ☆ 收藏这组单体（含 DFT 结果）
                  </Button>
                  <span className="text-xs text-muted-foreground">
                    任意双分子模式无醛/胺单体组归属，暂不支持收藏与追加 DFT 记录
                  </span>
                </div>
              ) : (
              <div className="flex flex-wrap items-center gap-2">
                {fav ? (
                  <>
                    <Button variant="secondary" onClick={handleMerge} disabled={merging}>
                      {merging
                        ? '合并中…'
                        : fav.has_dft
                          ? '★ 更新已有收藏的 DFT 结果'
                          : '☆ 合并 DFT 结果到已有收藏'}
                    </Button>
                    {fav.folder_name && (
                      <span className="text-xs text-muted-foreground">
                        该组合已在收藏夹「{fav.folder_name}」中
                      </span>
                    )}
                  </>
                ) : pairFavorite ? (
                  <span className="text-xs text-muted-foreground">
                    该组合已收藏（{pairFavorite.aldehyde?.name || '未知醛'} ×{' '}
                    {pairFavorite.amine?.name || '未知胺'}）
                  </span>
                ) : (
                  <Button variant="outline" onClick={handleFavorite} disabled={favoriting}>
                    {favoriting ? '处理中…' : '☆ 收藏这组单体（含 DFT 结果）'}
                  </Button>
                )}
                {/* 已收藏组合：追加本次结果到收藏的 DFT 分条记录 */}
                {appendTargetId && (
                  <Button
                    variant="outline"
                    onClick={() => void handleAppendDftEntry()}
                    disabled={appending}
                    title="将本次计算结果追加为该收藏的一条 DFT 记录"
                  >
                    {appending ? '追加中…' : '追加到收藏的 DFT 记录'}
                  </Button>
                )}
              </div>
              )}

              {/* 单体性质卡 */}
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <MonomerPropsCard
                  title={result.mode === 'pair' ? '分子 A 性质' : '醛单体性质'}
                  name={monoA.name || undefined}
                  loading={aProps.loading}
                  error={aProps.error}
                  props={aProps.data}
                />
                <MonomerPropsCard
                  title={result.mode === 'pair' ? '分子 B 性质' : '胺单体性质'}
                  name={monoB.name || undefined}
                  loading={bProps.loading}
                  error={bProps.error}
                  props={bProps.data}
                />
              </div>
            </>
          )}
        </div>
      </div>

      {/* 收藏前：选择目标收藏夹（可新建） */}
      <FavoriteFolderDialog
        open={favDialogOpen}
        onOpenChange={setFavDialogOpen}
        title="收藏这组单体"
        description="将当前醛/胺组合收藏到所选收藏夹，并携带本次 DFT 计算结果。"
        confirmLabel="收藏"
        submitting={favoriting}
        onConfirm={handleConfirmFavorite}
      />

      {/* 409 冲突：合并 DFT 结果到已有收藏 */}
      <Dialog open={mergeTarget !== null} onOpenChange={(v) => !v && setMergeTarget(null)}>
        <DialogContent className="w-[calc(100vw-1.5rem)] sm:max-w-md">
          <DialogHeader>
            <DialogTitle>已收藏过该组合</DialogTitle>
            <DialogDescription className="space-y-1 text-left">
              <span className="block">
                「{mergeTarget?.aldehyde_name || '未知单体'} × {mergeTarget?.amine_name || '未知单体'}」
                已在收藏夹「{mergeTarget?.folder_name || '收藏夹1'}」中，未重复创建。
              </span>
              <span className="block text-xs">
                已有 DFT 快照：{mergeTarget?.has_dft ? '有（合并将覆盖）' : '无'}
              </span>
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="outline" onClick={() => setMergeTarget(null)}>取消</Button>
            <Button onClick={() => void handleMerge()} disabled={merging}>
              {merging ? '合并中…' : '合并 DFT 结果到已有收藏'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
