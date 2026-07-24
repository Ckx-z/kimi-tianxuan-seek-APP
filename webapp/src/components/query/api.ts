/**
 * 查询打分页本地 API 辅助（api.ts 缺失的端点在此补充，不修改共享 api.ts）
 * 错误处理约定同 @/lib/api：网络失败抛 BackendUnavailableError；HTTP 错误提取 detail；
 * 默认弹中文 toast（silent 可关）。
 */
import { toast } from 'sonner';
import { BackendUnavailableError } from '@/lib/api';

const BASE = '/api';

async function request<T>(path: string, options: { method?: string; body?: unknown; silent?: boolean } = {}): Promise<T> {
  const { method = 'GET', body, silent = false } = options;
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    const err = new BackendUnavailableError();
    if (!silent) toast.error(err.message);
    throw err;
  }
  if (!res.ok) {
    let message = `请求失败（${res.status}）`;
    try {
      const data = await res.json();
      if (typeof data?.detail === 'string') message = data.detail;
      else if (typeof data?.message === 'string') message = data.message;
    } catch {
      // 响应体非 JSON，保留默认提示
    }
    if (!silent) toast.error(message);
    throw new Error(message);
  }
  return (await res.json()) as T;
}

// ---------- 类型（与后端契约对齐） ----------

/** 打分响应（OOD=out 时 score 及分量均为 null） */
export interface PredictResult {
  score: number | null;
  score_policy: string;
  score_source?: string;
  tree_score: number | null;
  tree_std: number | null;
  tree_model_name?: string | null;
  tree_route?: string | null;
  gnn_score: number | null;
  gnn_std: number | null;
  ood: { level: string; reasons: string[] };
  explanation?: ScoreExplanation;
}

/** 打分理由（SHAP 归因或全局特征重要性回退） */
export interface ScoreExplanation {
  method: 'shap' | 'global_importance' | 'none';
  items: {
    feature: string;
    label: string;
    value?: number | string | null;
    weight: number;
    direction?: string;
  }[];
  route_reason?: string;
  dominant_side?: string;
  note?: string;
}

/** 内置单体库条目（data/builtin_monomers.json，含 name/role/cas/smiles） */
export interface BuiltinMonomer {
  name: string;
  smiles: string;
  cas?: string;
  role?: string;
}

export interface MonomerLibrary {
  aldehydes: BuiltinMonomer[];
  amines: BuiltinMonomer[];
}

/** 单体性质卡（facts 来自 RDKit，narrative 来自 LLM，可空） */
export interface MonomerProps {
  facts: Record<string, number | string>;
  narrative: string | null;
  narrative_source: 'llm' | 'none';
}

/** 方案卡模板摘要 */
export interface PlanTemplateItem {
  id: string;
  name: string;
  source?: string;
  builtin?: boolean;
}

/** 方案卡 */
export interface PlanCardData {
  template: string;
  aldehyde?: { smiles: string; cas?: string; name?: string };
  amine?: { smiles: string; cas?: string; name?: string };
  conditions: Record<string, string | number>;
  defaults_note?: string;
  steps: string[];
  checklist: { item: string; detail?: string }[];
  monomer_hints: string[];
  generated_at?: string;
}

// ---------- 端点 ----------

/** 静默健康检查 */
export const checkHealth = () => request<{ status: string }>('/health', { silent: true });

/** 单对打分 */
export const predictPair = (aldSmiles: string, amineSmiles: string) =>
  request<PredictResult>('/predict', {
    method: 'POST',
    body: { ald_smiles: aldSmiles, amine_smiles: amineSmiles },
  });

/** 内置单体库（分组醛/胺） */
export const fetchMonomers = () => request<MonomerLibrary>('/monomers');

/** 单体性质卡 */
export const fetchMonomerProps = (smiles: string, name = '') =>
  request<MonomerProps>(`/monomers/props?smiles=${encodeURIComponent(smiles)}&name=${encodeURIComponent(name)}`);

/** 方案卡模板列表 */
export const fetchPlanTemplates = async (): Promise<PlanTemplateItem[]> => {
  const data = await request<{ templates: PlanTemplateItem[] }>('/plan-templates');
  return data.templates ?? [];
};

/** 生成方案卡（template_id 为空则用内置默认模板） */
export const fetchPlanCard = (payload: {
  aldehyde_smiles: string;
  amine_smiles: string;
  ald_name?: string;
  amine_name?: string;
  template_id?: string | null;
}) => request<PlanCardData>('/plan-card', { method: 'POST', body: payload });

/** 上传 docx 文献提取为方案卡模板（multipart，与 api.ts 的 JSON 封装不同，单独实现） */
export async function uploadPlanTemplate(file: File): Promise<PlanTemplateItem> {
  const form = new FormData();
  form.append('file', file);
  let res: Response;
  try {
    res = await fetch(`${BASE}/plan-templates/upload`, { method: 'POST', body: form });
  } catch {
    const err = new BackendUnavailableError();
    toast.error(err.message);
    throw err;
  }
  if (!res.ok) {
    let message = `上传失败（${res.status}）`;
    try {
      const data = await res.json();
      if (typeof data?.detail === 'string') message = data.detail;
    } catch {
      // 保留默认提示
    }
    toast.error(message);
    throw new Error(message);
  }
  return (await res.json()) as PlanTemplateItem;
}

/** 收藏一组单体 */
export const createFavorite = (payload: {
  aldehyde_smiles: string;
  amine_smiles: string;
  ald_name?: string;
  amine_name?: string;
}) => request('/favorites', { method: 'POST', body: payload, silent: true });

// ---------- 收藏状态 / 取消收藏 / 查询历史 ----------

/** 收藏条目（仅取查询页所需字段） */
export interface FavoriteItem {
  id: string;
  aldehyde_smiles: string;
  amine_smiles: string;
}

/** 收藏列表（静默，失败时调用方自行降级） */
export const fetchFavorites = async (): Promise<FavoriteItem[]> => {
  const data = await request<{ favorites: FavoriteItem[] }>('/favorites', { silent: true });
  return data.favorites ?? [];
};

/** 取消收藏 */
export const deleteFavorite = (id: string) =>
  request<void>(`/favorites/${id}`, { method: 'DELETE' });

/** 查询历史条目（data/prediction_log.jsonl 的 prediction 记录） */
export interface PredictHistoryEntry {
  timestamp?: string;
  ald_smiles: string;
  amine_smiles: string;
  score: number | null;
  tree_score?: number | null;
  gnn_score?: number | null;
  std?: number | null;
  arm?: string | null;
  route?: string | null;
  ood_level?: string;
  score_policy?: string;
  source?: string;
}

/** 查询历史（新→旧） */
export const fetchPredictHistory = async (limit = 50): Promise<PredictHistoryEntry[]> => {
  const data = await request<{ history: PredictHistoryEntry[] }>(
    `/predict/history?limit=${limit}`, { silent: true });
  return data.history ?? [];
};
