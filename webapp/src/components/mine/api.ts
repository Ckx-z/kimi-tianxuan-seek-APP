/**
 * 「我的」页本地 API 辅助（不修改共享 @/lib/api）
 * - 后端真实返回为包络结构（{favorites}/{records}/{plans}/{suggestions}），
 *   与共享 api.ts 的类型假设不一致，因此本页单独封装并按真实契约解包。
 * - 错误处理约定与 @/lib/api 一致：失败弹中文 toast 并抛出 Error；
 *   网络层失败抛 BackendUnavailableError 供页面优雅降级。
 */
import { toast } from 'sonner';
import { BackendUnavailableError } from '@/lib/api';

const BASE = '/api';

async function request<T>(path: string, init?: RequestInit, silent = false): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
      ...init,
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
    } catch {
      /* 非 JSON 响应 */
    }
    if (!silent) toast.error(message);
    throw new Error(message);
  }
  return (await res.json()) as T;
}

// ---------- 后端真实类型 ----------

/** 单体信息（收藏/方案内嵌结构） */
export interface MonomerInfo {
  smiles?: string;
  cas?: string;
  name?: string;
}

/** 文献引用条目（auto-matched 结构） */
export interface ReferenceItem {
  title?: string;
  doi?: string;
  source?: string;
  path_or_url?: string;
  match_type?: string; // both | aldehyde | amine
  count?: number;
  note?: string;
}

/** 预测快照（latest_prediction，结构与 /api/predict 响应一致） */
export interface PredictionSnapshot {
  score?: number | null;
  score_policy?: string;
  tree_score?: number | null;
  gnn_score?: number | null;
  tree_std?: number | null;
  std?: number | null;
  arm?: string;
  gnn_std?: number | null;
  tree_model_name?: string | null;
  tree_route?: string | null;
  ood?: { level?: string; reasons?: string[] } | string;
  date?: string;
  [key: string]: unknown;
}

/** 收藏条目（favorites/store.py 落盘结构） */
export interface FavoriteItem {
  id: string;
  aldehyde?: MonomerInfo;
  amine?: MonomerInfo;
  created_at?: string;
  notes?: string;
  latest_prediction?: PredictionSnapshot | null;
  references?: ReferenceItem[];
  experiment_record_ids?: string[];
}

/** 实验记录条目（records/store.py 落盘结构） */
export interface RecordItem {
  record_id: string;
  experiment_no?: string;
  date?: string;
  favorite_id?: string | null;
  conditions?: Record<string, unknown>;
  outcome?: Record<string, unknown>;
  strength?: string;
  notes?: string;
  operator?: string;
}

/** 迭代方案（data/generated_plans/plan_*.json 结构） */
export interface PlanItem {
  plan_id: string;
  seq?: number;
  favorite_id?: string;
  template_name?: string;
  created_at?: string;
  plan_card?: {
    template?: string;
    aldehyde?: MonomerInfo;
    amine?: MonomerInfo;
    conditions?: Record<string, unknown>;
    steps?: string[];
    defaults_note?: string;
    [key: string]: unknown;
  } | null;
  [key: string]: unknown;
}

/** 迭代建议（导出备份用，宽松结构） */
export interface SuggestionItem {
  suggestion_id?: string;
  [key: string]: unknown;
}

// ---------- 接口 ----------

export async function fetchFavorites(): Promise<FavoriteItem[]> {
  const data = await request<{ favorites?: FavoriteItem[] }>('/favorites');
  return Array.isArray(data?.favorites) ? data.favorites : [];
}

export async function fetchRecordsByFavorite(favoriteId: string): Promise<RecordItem[]> {
  const data = await request<{ records?: RecordItem[] }>(
    `/records?favorite_id=${encodeURIComponent(favoriteId)}`,
  );
  return Array.isArray(data?.records) ? data.records : [];
}

export async function fetchAllRecords(): Promise<RecordItem[]> {
  const data = await request<{ records?: RecordItem[] }>('/records');
  return Array.isArray(data?.records) ? data.records : [];
}

export async function deleteFavorite(id: string): Promise<void> {
  await request(`/favorites/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export async function fetchPlans(): Promise<PlanItem[]> {
  const data = await request<{ plans?: PlanItem[] }>('/iterate/plans');
  return Array.isArray(data?.plans) ? data.plans : [];
}

export async function fetchSuggestions(): Promise<SuggestionItem[]> {
  const data = await request<{ suggestions?: SuggestionItem[] }>('/iterate/suggestions');
  return Array.isArray(data?.suggestions) ? data.suggestions : [];
}
