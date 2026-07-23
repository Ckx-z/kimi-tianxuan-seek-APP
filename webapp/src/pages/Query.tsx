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
  fetchMonomerProps,
  fetchMonomers,
  fetchPlanCard,
  fetchPlanTemplates,
  predictPair,
  type MonomerLibrary,
  type MonomerProps,
  type PlanCardData,
  type PlanTemplateItem,
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

  /** 收藏这组单体 */
  const handleFavorite = async () => {
    setFavoriting(true);
    try {
      await createFavorite({
        aldehyde_smiles: ald.smiles,
        amine_smiles: amine.smiles,
        ald_name: ald.name,
        amine_name: amine.name,
      });
      toast.success('已收藏这组单体');
    } catch (e) {
      toast.error(`收藏失败：${e instanceof Error ? e.message : '未知错误'}`);
    } finally {
      setFavoriting(false);
    }
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
        </div>

        {/* 右侧结果区（2/3） */}
        <div className="space-y-4 lg:col-span-2">
          <ResultCard result={result} loading={predicting} />

          {/* 收藏按钮（有打分结果后出现） */}
          {result && !predicting && (
            <Button variant="outline" onClick={handleFavorite} disabled={favoriting}>
              {favoriting ? '收藏中…' : '★ 收藏这组单体'}
            </Button>
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
