/**
 * 查询打分页（任务 A）
 * 布局：左侧输入区 1/3（醛/胺三通道输入 + 开始打分），右侧结果区 2/3
 * （打分结果大卡 + 收藏按钮 + 单体性质卡×2 + 方案卡）。响应式堆叠。
 * 后端未连接时显示降级提示，不白屏。
 */
import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import MonomerInput, { type MonomerValue } from '@/components/query/MonomerInput';
import ResultCard from '@/components/query/ResultCard';
import MonomerPropsCard from '@/components/query/MonomerPropsCard';
import PlanCardPanel from '@/components/query/PlanCardPanel';
import {
  checkHealth,
  createFavorite,
  deleteFavorite,
  fetchFavorites,
  fetchMonomerProps,
  fetchMonomers,
  fetchPlanCard,
  fetchPlanTemplates,
  fetchPredictHistory,
  predictPair,
  type FavoriteItem,
  type MonomerLibrary,
  type MonomerProps,
  type PlanCardData,
  type PlanTemplateItem,
  type PredictHistoryEntry,
  type PredictResult,
} from '@/components/query/api';

/** 单侧性质卡状态 */
interface PropsState {
  loading: boolean;
  error: string | null;
  data: MonomerProps | null;
}
const emptyProps: PropsState = { loading: false, error: null, data: null };

export default function Query() {
  // ---------- 输入 ----------
  const [ald, setAld] = useState<MonomerValue>({ smiles: '', name: '' });
  const [amine, setAmine] = useState<MonomerValue>({ smiles: '', name: '' });

  // ---------- 后端状态 ----------
  const [backendDown, setBackendDown] = useState(false);
  const [library, setLibrary] = useState<MonomerLibrary>({ aldehydes: [], amines: [] });
  const [libraryLoading, setLibraryLoading] = useState(true);

  // ---------- 打分结果 ----------
  const [predicting, setPredicting] = useState(false);
  const [result, setResult] = useState<PredictResult | null>(null);

  // ---------- 联动卡片 ----------
  const [aldProps, setAldProps] = useState<PropsState>(emptyProps);
  const [amineProps, setAmineProps] = useState<PropsState>(emptyProps);
  const [planCard, setPlanCard] = useState<PlanCardData | null>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const [templates, setTemplates] = useState<PlanTemplateItem[]>([]);
  const [templatesLoading, setTemplatesLoading] = useState(true);
  const [templateId, setTemplateId] = useState(''); // '' = 内置默认模板
  const [favoriting, setFavoriting] = useState(false);
  const [favorites, setFavorites] = useState<FavoriteItem[]>([]);
  const [history, setHistory] = useState<PredictHistoryEntry[]>([]);

  /** 当前输入组合是否已收藏（返回收藏条目，未收藏为 null） */
  const matchedFavorite = (() => {
    if (!ald.smiles || !amine.smiles) return null;
    return favorites.find(
      (f) => f.aldehyde_smiles === ald.smiles && f.amine_smiles === amine.smiles
    ) ?? null;
  })();

  /** 刷新收藏列表与查询历史（静默失败） */
  const refreshFavorites = useCallback(() => {
    fetchFavorites().then(setFavorites).catch(() => {});
  }, []);
  const refreshHistory = useCallback(() => {
    fetchPredictHistory().then(setHistory).catch(() => {});
  }, []);

  // 健康检查 + 初始数据（单体库、模板列表）
  useEffect(() => {
    checkHealth()
      .then(() => setBackendDown(false))
      .catch(() => setBackendDown(true));

    fetchMonomers()
      .then(setLibrary)
      .catch(() => setLibrary({ aldehydes: [], amines: [] }))
      .finally(() => setLibraryLoading(false));

    fetchPlanTemplates()
      .then(setTemplates)
      .catch(() => setTemplates([]))
      .finally(() => setTemplatesLoading(false));

    refreshFavorites();
    refreshHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** 加载单侧性质卡 */
  const loadProps = useCallback(
    (smiles: string, name: string, setter: React.Dispatch<React.SetStateAction<PropsState>>) => {
      setter({ loading: true, error: null, data: null });
      fetchMonomerProps(smiles, name)
        .then((data) => setter({ loading: false, error: null, data }))
        .catch((e) =>
          setter({ loading: false, error: e instanceof Error ? e.message : '未知错误', data: null })
        );
    },
    []
  );

  /** 生成方案卡（可随模板切换重生成） */
  const loadPlanCard = useCallback(
    (aldV: MonomerValue, amineV: MonomerValue, tplId: string) => {
      setPlanLoading(true);
      setPlanError(null);
      fetchPlanCard({
        aldehyde_smiles: aldV.smiles,
        amine_smiles: amineV.smiles,
        ald_name: aldV.name,
        amine_name: amineV.name,
        template_id: tplId || null,
      })
        .then((card) => setPlanCard(card))
        .catch((e) => setPlanError(e instanceof Error ? e.message : '未知错误'))
        .finally(() => setPlanLoading(false));
    },
    []
  );

  /** 开始打分 */
  const handlePredict = async () => {
    if (!ald.smiles || !amine.smiles) {
      toast.warning('请先填写醛和胺单体的 SMILES');
      return;
    }
    setPredicting(true);
    setResult(null);
    setAldProps(emptyProps);
    setAmineProps(emptyProps);
    setPlanCard(null);
    setPlanError(null);
    try {
      const r = await predictPair(ald.smiles, amine.smiles);
      setResult(r);
      toast.success('打分完成');
      refreshHistory();
      // 联动：性质卡（醛/胺各一）+ 方案卡
      loadProps(ald.smiles, ald.name, setAldProps);
      loadProps(amine.smiles, amine.name, setAmineProps);
      loadPlanCard(ald, amine, templateId);
    } catch {
      // toast 已在 api 辅助中弹出；result 保持 null 显示空态
    } finally {
      setPredicting(false);
    }
  };

  /** 切换模板 → 若已有打分结果则重新生成方案卡 */
  const handleTemplateChange = (id: string) => {
    setTemplateId(id);
    if (result && ald.smiles && amine.smiles) loadPlanCard(ald, amine, id);
  };

  /** docx 上传成功 → 加入模板列表并选中 */
  const handleTemplateUploaded = (tpl: PlanTemplateItem) => {
    setTemplates((prev) => [...prev.filter((t) => t.id !== tpl.id), tpl]);
    handleTemplateChange(tpl.id);
  };

  /** 收藏 / 取消收藏这组单体 */
  const handleFavorite = async () => {
    setFavoriting(true);
    try {
      if (matchedFavorite) {
        await deleteFavorite(matchedFavorite.id);
        toast.success('已取消收藏');
      } else {
        await createFavorite({
          aldehyde_smiles: ald.smiles,
          amine_smiles: amine.smiles,
          ald_name: ald.name,
          amine_name: amine.name,
        });
        toast.success('已收藏这组单体');
      }
      refreshFavorites();
    } catch (e) {
      toast.error(`操作失败：${e instanceof Error ? e.message : '未知错误'}`);
    } finally {
      setFavoriting(false);
    }
  };

  /** 点击历史记录：完整回显当时输入与全部结果 */
  const handleHistoryClick = (h: PredictHistoryEntry) => {
    setAld({ smiles: h.ald_smiles, name: '' });
    setAmine({ smiles: h.amine_smiles, name: '' });
    setResult({
      score: h.score ?? null,
      score_policy: h.score_policy ?? 'max_tree_gnn',
      tree_score: h.tree_score ?? null,
      tree_std: h.std ?? null,
      tree_model_name: h.arm ?? null,
      tree_route: h.route ?? null,
      gnn_score: h.gnn_score ?? null,
      gnn_std: null,
      ood: { level: h.ood_level ?? 'none', reasons: [] },
    });
    setPlanCard(null);
    setPlanError(null);
    loadProps(h.ald_smiles, '', setAldProps);
    loadProps(h.amine_smiles, '', setAmineProps);
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold text-foreground">查询打分</h1>

      {/* 后端降级提示（不阻塞界面，不白屏） */}
      {backendDown && (
        <Alert className="border-yellow-300 bg-yellow-50 dark:border-yellow-900 dark:bg-yellow-950/40">
          <AlertTitle className="text-yellow-800 dark:text-yellow-300">后端未连接</AlertTitle>
          <AlertDescription className="text-yellow-700 dark:text-yellow-400">
            无法连接 FastAPI 服务（http://localhost:8000）。单体库、打分、方案卡等功能暂不可用；
            CAS 号解析（PubChem）不受影响。请启动后端后刷新页面。
          </AlertDescription>
        </Alert>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* 左侧输入区（1/3） */}
        <div className="space-y-4">
          <MonomerInput
            title="醛单体"
            role="aldehyde"
            value={ald}
            onChange={setAld}
            library={library.aldehydes}
            libraryLoading={libraryLoading}
            disabled={predicting}
          />
          <MonomerInput
            title="胺单体"
            role="amine"
            value={amine}
            onChange={setAmine}
            library={library.amines}
            libraryLoading={libraryLoading}
            disabled={predicting}
          />
          <Button
            className="w-full"
            size="lg"
            onClick={handlePredict}
            disabled={predicting || !ald.smiles || !amine.smiles}
          >
            {predicting ? '打分中…' : '开始打分'}
          </Button>

          {/* 查询历史：点击完整回显当时输入与全部结果 */}
          {history.length > 0 && (
            <div className="rounded-lg border bg-card p-3 text-card-foreground shadow-sm">
              <h3 className="mb-2 text-sm font-medium">查询历史</h3>
              <ul className="max-h-72 space-y-1 overflow-y-auto text-sm">
                {history.map((h, i) => (
                  <li key={`${h.timestamp ?? ''}-${i}`}>
                    <button
                      type="button"
                      onClick={() => handleHistoryClick(h)}
                      className="w-full rounded px-2 py-1 text-left hover:bg-accent"
                      title={`${h.ald_smiles} + ${h.amine_smiles}`}
                    >
                      <span className="mr-2 text-xs text-muted-foreground">
                        {(h.timestamp ?? '').replace('T', ' ').slice(0, 19)}
                      </span>
                      <span className="font-mono text-xs">
                        {h.ald_smiles.slice(0, 14)}… + {h.amine_smiles.slice(0, 14)}…
                      </span>
                      <span className="float-right font-medium">
                        {h.score != null ? h.score.toFixed(3) : '⛔'}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {/* 右侧结果区（2/3） */}
        <div className="space-y-4 lg:col-span-2">
          <ResultCard result={result} loading={predicting} />

          {/* 收藏按钮（有打分结果后出现）；已收藏则置灰显示，可再次点击取消 */}
          {result && !predicting && (
            <Button
              variant={matchedFavorite ? 'secondary' : 'outline'}
              onClick={handleFavorite}
              disabled={favoriting}
              className={matchedFavorite ? 'opacity-70' : ''}
            >
              {favoriting
                ? '处理中…'
                : matchedFavorite
                  ? '★ 已收藏（点击取消收藏）'
                  : '☆ 收藏这组单体'}
            </Button>
          )}

          {/* 打分理由：哪部分特征推高/拉低打分（SHAP 归因或全局重要性回退） */}
          {result && !predicting && result.explanation && result.explanation.method !== 'none' && (
            <div className="rounded-lg border bg-card p-4 text-card-foreground shadow-sm">
              <h3 className="mb-1 font-medium">打分理由</h3>
              {result.explanation.note && (
                <p className="mb-2 text-xs text-muted-foreground">{result.explanation.note}</p>
              )}
              <ul className="space-y-1 text-sm">
                {result.explanation.items.map((it) => (
                  <li key={it.feature} className="flex items-center justify-between gap-2">
                    <span>{it.label}</span>
                    <span className="text-muted-foreground">
                      {it.direction && (
                        <span className={it.direction === '推高' ? 'text-green-700 dark:text-green-400' : 'text-red-700 dark:text-red-400'}>
                          {it.direction}{' '}
                        </span>
                      )}
                      {result.explanation!.method !== 'global_importance' && it.direction
                        ? `SHAP ${it.weight > 0 ? '+' : ''}${it.weight.toFixed(3)}`
                        : `重要性 ${it.weight.toFixed(3)}`}
                    </span>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-muted-foreground">
                {result.explanation.dominant_side && `主导贡献方：${result.explanation.dominant_side}。`}
                {result.explanation.route_reason && `模型路由：${result.explanation.route_reason}`}
              </p>
            </div>
          )}

          {/* 化学结构：醛/胺单体 2D 结构图 + 缩合产物（二聚体骨架）示意图 */}
          {result && !predicting && ald.smiles && amine.smiles && (
            <div className="rounded-lg border bg-card p-4 text-card-foreground shadow-sm">
              <h3 className="mb-2 font-medium">化学结构</h3>
              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                {[
                  { src: `/api/monomers/structure.svg?smiles=${encodeURIComponent(ald.smiles)}`, label: '醛单体' },
                  { src: `/api/monomers/structure.svg?smiles=${encodeURIComponent(amine.smiles)}`, label: '胺单体' },
                  { src: `/api/monomers/dimer.svg?ald=${encodeURIComponent(ald.smiles)}&amine=${encodeURIComponent(amine.smiles)}`, label: '缩合产物（示意）' },
                ].map((im) => (
                  <figure key={im.label} className="text-center">
                    <img
                      src={im.src}
                      alt={im.label}
                      className="mx-auto max-h-44 rounded border bg-white object-contain p-1"
                      onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
                    />
                    <figcaption className="mt-1 text-xs text-muted-foreground">{im.label}</figcaption>
                  </figure>
                ))}
              </div>
            </div>
          )}

          {/* 单体性质卡（醛/胺各一） */}
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <MonomerPropsCard
              title="醛单体性质"
              name={ald.name || undefined}
              loading={aldProps.loading}
              error={aldProps.error}
              props={aldProps.data}
            />
            <MonomerPropsCard
              title="胺单体性质"
              name={amine.name || undefined}
              loading={amineProps.loading}
              error={amineProps.error}
              props={amineProps.data}
            />
          </div>

          {/* 方案卡 */}
          <PlanCardPanel
            card={planCard}
            loading={planLoading}
            error={planError}
            templates={templates}
            templatesLoading={templatesLoading}
            templateId={templateId}
            onTemplateChange={handleTemplateChange}
            onTemplateUploaded={handleTemplateUploaded}
            disabled={!result || predicting}
          />
        </div>
      </div>
    </div>
  );
}
