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

/** DFT 快照（预留结构：本期仅字段透传，DFT 计算后续批次接入） */
export type DftSnapshot = Record<string, unknown>;

/** 收藏条目（favorites/store.py 落盘结构） */
export interface FavoriteItem {
  id: string;
  /** 归属收藏夹 id（旧数据经后端迁移归「收藏夹1」） */
  folder_id?: string;
  aldehyde?: MonomerInfo;
  amine?: MonomerInfo;
  created_at?: string;
  notes?: string;
  latest_prediction?: PredictionSnapshot | null;
  dft_snapshot?: DftSnapshot | null;
  references?: ReferenceItem[];
  experiment_record_ids?: string[];
}

/** 收藏夹（favorite_folders.json 结构，列表接口附带收藏数） */
export interface FolderItem {
  id: string;
  name: string;
  created_at?: string;
  favorite_count?: number;
}

/** 实验记录条目（records/store.py 落盘结构，仅「我的数据」计数/导出用；
 *  详情展示统一使用 @/components/records/api 的完整 RecordItem） */
export interface RecordItem {
  record_id: string;
  experiment_no?: string;
  date?: string;
  status?: 'draft' | 'final';
  favorite_id?: string | null;
  conditions?: Record<string, unknown>;
  outcome?: 'film' | 'partial' | 'failed' | '';
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

/** 方案卡模板（plan_templates.py 契约） */
export interface PlanTemplateItem {
  id: string;
  name: string;
  source?: string;
  builtin?: boolean;
}

// ---------- 接口 ----------

export async function fetchFavorites(): Promise<FavoriteItem[]> {
  const data = await request<{ favorites?: FavoriteItem[] }>('/favorites');
  return Array.isArray(data?.favorites) ? data.favorites : [];
}

export async function fetchAllRecords(): Promise<RecordItem[]> {
  const data = await request<{ records?: RecordItem[] }>('/records');
  return Array.isArray(data?.records) ? data.records : [];
}

export async function deleteFavorite(id: string): Promise<void> {
  await request(`/favorites/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

/** 单条收藏（一键打分后局部刷新用） */
export async function fetchFavorite(id: string): Promise<FavoriteItem> {
  return request<FavoriteItem>(`/favorites/${encodeURIComponent(id)}`);
}

// ---------- 收藏夹 Folder（P2 收藏夹体系） ----------

export async function fetchFolders(): Promise<FolderItem[]> {
  const data = await request<{ folders?: FolderItem[] }>('/favorite-folders');
  return Array.isArray(data?.folders) ? data.folders : [];
}

export async function createFolder(name: string): Promise<FolderItem> {
  return request<FolderItem>('/favorite-folders', {
    method: 'POST',
    body: JSON.stringify({ name }),
  });
}

export async function renameFolder(id: string, name: string): Promise<FolderItem> {
  return request<FolderItem>(`/favorite-folders/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ name }),
  });
}

/** 删夹连带删内收藏，返回删除的收藏条数 */
export async function deleteFolder(id: string): Promise<number> {
  const data = await request<{ deleted_favorites?: number }>(
    `/favorite-folders/${encodeURIComponent(id)}`,
    { method: 'DELETE' },
  );
  return data?.deleted_favorites ?? 0;
}

/** 收藏局部更新：移夹 / 改备注 / 写入 DFT 快照 */
export async function updateFavorite(
  id: string,
  fields: { folder_id?: string; notes?: string; dft_snapshot?: DftSnapshot },
): Promise<FavoriteItem> {
  return request<FavoriteItem>(`/favorites/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(fields),
  });
}

export async function fetchPlans(): Promise<PlanItem[]> {
  const data = await request<{ plans?: PlanItem[] }>('/iterate/plans');
  return Array.isArray(data?.plans) ? data.plans : [];
}

export async function fetchSuggestions(): Promise<SuggestionItem[]> {
  const data = await request<{ suggestions?: SuggestionItem[] }>('/iterate/suggestions');
  return Array.isArray(data?.suggestions) ? data.suggestions : [];
}

// ---------- 方案模板管理（「我的方案库」区块用） ----------

export async function fetchPlanTemplates(): Promise<PlanTemplateItem[]> {
  const data = await request<{ templates?: PlanTemplateItem[] }>('/plan-templates');
  return Array.isArray(data?.templates) ? data.templates : [];
}

/** 上传 docx 文献 → LLM 提取为方案模板（multipart；name 走 query 参数） */
export async function uploadPlanTemplate(file: File, name = ''): Promise<PlanTemplateItem> {
  const form = new FormData();
  form.append('file', file);
  const qs = name.trim() ? `?name=${encodeURIComponent(name.trim())}` : '';
  let res: Response;
  try {
    res = await fetch(`${BASE}/plan-templates/upload${qs}`, { method: 'POST', body: form });
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
      /* 非 JSON 响应 */
    }
    toast.error(message);
    throw new Error(message);
  }
  return (await res.json()) as PlanTemplateItem;
}

/** 删除自定义方案模板（内置模板后端禁止删除） */
export async function deletePlanTemplate(id: string): Promise<void> {
  await request(`/plan-templates/${encodeURIComponent(id)}`, { method: 'DELETE' });
}
