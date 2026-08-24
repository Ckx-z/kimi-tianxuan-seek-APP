/**
 * DFT 计算页本地 API 辅助（端点契约与 api/routers/dft.py 对齐）
 * 错误处理约定同 query/api.ts：网络失败抛 BackendUnavailableError；HTTP 错误提取 detail。
 */
import { toast } from 'sonner';
import { BackendUnavailableError } from '@/lib/api';
import { DuplicateFavoriteError } from '@/components/query/api';

const BASE = '/api/dft';

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
    } catch {
      // 响应体非 JSON，保留默认提示
    }
    if (!silent) toast.error(message);
    throw new Error(message);
  }
  return (await res.json()) as T;
}

// ---------- 类型（与后端契约对齐） ----------

export type DftMethod = 'gfnff' | 'gfn2';
export type DftJobStatus = 'pending' | 'running' | 'done' | 'failed';

/** 已有收藏联动信息（result.favorite） */
export interface DftFavoriteInfo {
  id: string;
  folder_id?: string;
  folder_name?: string;
  aldehyde_name?: string;
  amine_name?: string;
  has_prediction?: boolean;
  has_dft?: boolean;
}

export interface DftResult {
  smiles_a: string;
  smiles_b: string;
  method: DftMethod;
  method_label: string;
  e_bind_hartree: number;
  e_bind_kcal: number;
  e_bind_kj: number;
  energies_hartree: { a: number; b: number; complex: number };
  gap_ev: { a: number | null; b: number | null; complex: number | null };
  dipole_debye: { a: number | null; b: number | null; complex: number | null };
  complex_xyz: string;
  elapsed_sec: number;
  cached: boolean;
  favorite: DftFavoriteInfo | null;
}

export interface DftJob {
  job_id: string;
  status: DftJobStatus;
  progress_hint: string;
  method: DftMethod;
  cached: boolean;
  result: DftResult | null;
  error: string | null;
  created_at?: string;
}

/** 历史条目（dft_log.jsonl） */
export interface DftHistoryEntry {
  timestamp?: string;
  smiles_a: string;
  smiles_b: string;
  method: DftMethod;
  status: 'done' | 'failed';
  error?: string;
  e_bind_kcal?: number;
  e_bind_kj?: number;
  gap_ev?: DftResult['gap_ev'];
  dipole_debye?: DftResult['dipole_debye'];
  energies_hartree?: DftResult['energies_hartree'];
  complex_xyz?: string;
  elapsed_sec?: number;
}

// ---------- 端点 ----------

/** 创建计算任务（202；缓存命中时返回的 job 直接 done 且 cached=true） */
export const createDftJob = (smilesA: string, smilesB: string, method: DftMethod) =>
  request<DftJob>('/jobs', { method: 'POST', body: { smiles_a: smilesA, smiles_b: smilesB, method } });

/** 轮询任务状态（静默：轮询期间失败由页面统一处理，不每跳弹 toast） */
export const fetchDftJob = (jobId: string) =>
  request<DftJob>(`/jobs/${encodeURIComponent(jobId)}`, { silent: true });

/** 计算历史（新→旧） */
export const fetchDftHistory = async (limit = 50): Promise<DftHistoryEntry[]> => {
  const data = await request<{ history: DftHistoryEntry[] }>(`/history?limit=${limit}`, { silent: true });
  return data.history ?? [];
};

/** 把 DFT 结果快照合并写入已有收藏（PATCH dft_snapshot） */
export function mergeDftToFavorite(favoriteId: string, result: DftResult) {
  return requestFavorite(favoriteId, buildDftSnapshot(result));
}

/** 由计算结果构造 dft_snapshot（落盘字段，「我的」页可展示） */
export function buildDftSnapshot(result: DftResult) {
  return {
    method: result.method,
    e_bind_kcal: result.e_bind_kcal,
    e_bind_kj: result.e_bind_kj,
    gap_ev: result.gap_ev,
    dipole_debye: result.dipole_debye,
    date: new Date().toISOString(),
  };
}

/** PATCH /api/favorites/{id}（dft_snapshot 专用轻封装） */
async function requestFavorite(favoriteId: string, dftSnapshot: unknown) {
  let res: Response;
  try {
    res = await fetch(`/api/favorites/${encodeURIComponent(favoriteId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dft_snapshot: dftSnapshot }),
    });
  } catch {
    const err = new BackendUnavailableError();
    toast.error(err.message);
    throw err;
  }
  if (!res.ok) {
    let message = `请求失败（${res.status}）`;
    try {
      const data = await res.json();
      if (typeof data?.detail === 'string') message = data.detail;
    } catch {
      // 保留默认提示
    }
    toast.error(message);
    throw new Error(message);
  }
  return res.json();
}

/** 收藏这组单体并携带 DFT 快照；同对已收藏抛 DuplicateFavoriteError（409） */
export async function createFavoriteWithDft(payload: {
  aldehyde_smiles: string;
  amine_smiles: string;
  ald_name?: string;
  amine_name?: string;
  dft_snapshot: unknown;
}): Promise<unknown> {
  let res: Response;
  try {
    res = await fetch('/api/favorites', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  } catch {
    const err = new BackendUnavailableError();
    toast.error(err.message);
    throw err;
  }
  if (res.status === 409) {
    let existing = { id: '' };
    try {
      const data = await res.json();
      if (data?.detail?.existing) existing = data.detail.existing;
    } catch {
      // 保留默认摘要
    }
    throw new DuplicateFavoriteError(existing);
  }
  if (!res.ok) {
    let message = `收藏失败（${res.status}）`;
    try {
      const data = await res.json();
      if (typeof data?.detail === 'string') message = data.detail;
    } catch {
      // 保留默认提示
    }
    toast.error(message);
    throw new Error(message);
  }
  return res.json();
}
