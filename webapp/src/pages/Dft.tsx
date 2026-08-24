/**
 * DFT 计算页（v1.0.0 工具箱·方案§三）
 * 布局对齐查询打分页：左侧输入区 1/3（两个单体三通道输入 + 方法档位 + 历史），
 * 右侧结果区 2/3（结合能大卡 + 描述符 + 结构图 + 性质卡 + 收藏联动）。
 * 异步任务轮询（1.5s），全程中文进度提示与失败原因。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Label } from '@/components/ui/label';
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group';
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
import MonomerPropsCard from '@/components/query/MonomerPropsCard';
import {
  DuplicateFavoriteError,
  fetchMonomerProps,
  fetchMonomers,
  type MonomerLibrary,
  type MonomerProps,
} from '@/components/query/api';
import DftResultPanel from '@/components/dft/DftResultPanel';
import {
  buildDftSnapshot,
  createDftJob,
  createFavoriteWithDft,
  fetchDftHistory,
  fetchDftJob,
  mergeDftToFavorite,
  type DftHistoryEntry,
  type DftMethod,
  type DftResult,
} from '@/components/dft/api';

interface PropsState {
  loading: boolean;
  error: string | null;
  data: MonomerProps | null;
}
const emptyProps: PropsState = { loading: false, error: null, data: null };

/** 历史条目回显 → 拼装成 DftResult（favorite 信息需重新计算时以 null 处理） */
function resultFromHistory(h: DftHistoryEntry): DftResult {
  return {
    smiles_a: h.smiles_a,
    smiles_b: h.smiles_b,
    method: h.method,
    method_label: h.method === 'gfnff' ? 'GFN-FF 力场（快速）' : 'GFN2-xTB（精确）',
    e_bind_hartree: 0,
    e_bind_kcal: h.e_bind_kcal ?? 0,
    e_bind_kj: h.e_bind_kj ?? 0,
    energies_hartree: h.energies_hartree ?? { a: 0, b: 0, complex: 0 },
    gap_ev: h.gap_ev ?? { a: null, b: null, complex: null },
    dipole_debye: h.dipole_debye ?? { a: null, b: null, complex: null },
    complex_xyz: h.complex_xyz ?? '',
    elapsed_sec: h.elapsed_sec ?? 0,
    cached: true,
    favorite: null,
  };
}

export default function Dft() {
  // ---------- 输入 ----------
  const [monoA, setMonoA] = useState<MonomerValue>({ smiles: '', name: '' });
  const [monoB, setMonoB] = useState<MonomerValue>({ smiles: '', name: '' });
  const [method, setMethod] = useState<DftMethod>('gfn2');

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

  // ---------- 联动 ----------
  const [aProps, setAProps] = useState<PropsState>(emptyProps);
  const [bProps, setBProps] = useState<PropsState>(emptyProps);
  const [history, setHistory] = useState<DftHistoryEntry[]>([]);
  const [favoriting, setFavoriting] = useState(false);
  /** 409 冲突时待合并的已有收藏摘要 */
  const [mergeTarget, setMergeTarget] = useState<{ id: string; folder_name?: string; aldehyde_name?: string; amine_name?: string; has_dft?: boolean } | null>(null);
  const [merging, setMerging] = useState(false);

  const refreshHistory = useCallback(() => {
    fetchDftHistory().then(setHistory).catch(() => {});
  }, []);

  useEffect(() => {
    fetchMonomers()
      .then((lib) => { setLibrary(lib); setBackendDown(false); })
      .catch(() => { setLibrary({ aldehydes: [], amines: [] }); setBackendDown(true); })
      .finally(() => setLibraryLoading(false));
    refreshHistory();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
      toast.warning('请先填写两个单体的 SMILES');
      return;
    }
    setRunning(true);
    setResult(null);
    setError(null);
    setAProps(emptyProps);
    setBProps(emptyProps);
    setProgressHint('正在提交计算任务…');
    try {
      const job = await createDftJob(monoA.smiles, monoB.smiles, method);
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

  // 结果就绪后联动性质卡
  useEffect(() => {
    if (result) {
      loadProps(result.smiles_a, monoA.name, setAProps);
      loadProps(result.smiles_b, monoB.name, setBProps);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result]);

  /** 收藏（无已有收藏时直接带 DFT 快照收藏；409 时弹合并对话框） */
  const handleFavorite = async () => {
    if (!result) return;
    setFavoriting(true);
    try {
      await createFavoriteWithDft({
        aldehyde_smiles: result.smiles_a,
        amine_smiles: result.smiles_b,
        ald_name: monoA.name,
        amine_name: monoB.name,
        dft_snapshot: buildDftSnapshot(result),
      });
      toast.success('已收藏这组单体（含 DFT 结果）');
      setResult({ ...result, favorite: { id: '', has_dft: true } });
    } catch (e) {
      if (e instanceof DuplicateFavoriteError) {
        setMergeTarget(e.existing);
      } else {
        toast.error(`收藏失败：${e instanceof Error ? e.message : '未知错误'}`);
      }
    } finally {
      setFavoriting(false);
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

  /** 历史点击：回显输入与结果 */
  const handleHistoryClick = (h: DftHistoryEntry) => {
    setMonoA({ smiles: h.smiles_a, name: '' });
    setMonoB({ smiles: h.smiles_b, name: '' });
    setMethod(h.method);
    setError(h.status === 'failed' ? (h.error || '计算失败') : null);
    setResult(h.status === 'done' ? resultFromHistory(h) : null);
    setRunning(false);
  };

  const fav = result?.favorite;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold text-gradient-royal">DFT 计算</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          半经验量子化学（xTB）计算两个 COF 单体的结合能，辅助判断缩合反应倾向
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

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* 左侧输入区 */}
        <div className="space-y-4">
          <MonomerInput
            title="单体 A"
            role="aldehyde"
            value={monoA}
            onChange={setMonoA}
            library={library.aldehydes}
            libraryLoading={libraryLoading}
            disabled={running}
          />
          <MonomerInput
            title="单体 B"
            role="amine"
            value={monoB}
            onChange={setMonoB}
            library={library.amines}
            libraryLoading={libraryLoading}
            disabled={running}
          />

          {/* 方法档位 */}
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
                    精确（GFN2-xTB 半经验）：通常数十秒，能量与电子结构更可靠。
                    两者均为半经验方法，精确能量请导出几何用 DFT 复算。
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

          <Button
            className="w-full"
            size="lg"
            onClick={handleSubmit}
            disabled={running || !monoA.smiles || !monoB.smiles}
          >
            {running ? '计算中…' : '开始计算'}
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
                      title={`${h.smiles_a} + ${h.smiles_b}`}
                    >
                      <span className="mr-2 text-xs text-muted-foreground">
                        {(h.timestamp ?? '').replace('T', ' ').slice(0, 19)}
                      </span>
                      <Badge variant="outline" className="mr-1 text-[10px]">
                        {h.method === 'gfnff' ? '快速' : '精确'}
                      </Badge>
                      <span className="font-mono text-xs">
                        {h.smiles_a.slice(0, 10)}… + {h.smiles_b.slice(0, 10)}…
                      </span>
                      <span className="float-right font-medium tabular-nums">
                        {h.status === 'done' && h.e_bind_kcal != null
                          ? `${h.e_bind_kcal.toFixed(1)} kcal`
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
                {method === 'gfn2' ? '精确档位通常需要数十秒，大体系可能数分钟' : '快速档位通常数秒内完成'}
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
              输入两个单体并选择方法档位后点击「开始计算」；
              结果包含结合能、能隙、偶极矩与优化后复合物几何。
            </div>
          )}

          {/* 结果 */}
          {result && !running && (
            <>
              <DftResultPanel result={result} smilesA={result.smiles_a} smilesB={result.smiles_b} />

              {/* 收藏联动 */}
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
                ) : (
                  <Button variant="outline" onClick={handleFavorite} disabled={favoriting}>
                    {favoriting ? '处理中…' : '☆ 收藏这组单体（含 DFT 结果）'}
                  </Button>
                )}
              </div>

              {/* 单体性质卡 */}
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <MonomerPropsCard
                  title="单体 A 性质"
                  name={monoA.name || undefined}
                  loading={aProps.loading}
                  error={aProps.error}
                  props={aProps.data}
                />
                <MonomerPropsCard
                  title="单体 B 性质"
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
