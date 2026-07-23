/**
 * 实验记录页本地 API 辅助（不修改共享 @/lib/api）
 * 说明：后端 GET /api/records 返回 { records: [...] }、GET /api/favorites 返回 { favorites: [...] }，
 * 与共享 api.ts 中 recordsApi.list 的裸数组假设不一致，因此本页自建 fetch 封装。
 * 错误处理约定与 @/lib/api 一致：失败弹中文 toast 并抛出 Error。
 */
import { toast } from 'sonner';

/** 后端不可用时抛出此错误，调用方据此做降级展示 */
export class BackendUnavailableError extends Error {
  constructor(message = '后端未连接，请确认 FastAPI 服务已启动（http://localhost:8000）') {
    super(message);
    this.name = 'BackendUnavailableError';
  }
}

/** 统一请求封装 */
async function request<T>(path: string, options: { method?: string; body?: unknown; silent?: boolean } = {}): Promise<T> {
  const { method = 'GET', body, silent = false } = options;
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
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
      // 非 JSON 响应，保留默认提示
    }
    if (!silent) toast.error(message);
    throw new Error(message);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ---------- 本地类型（与后端 records/favorites 存储契约一致） ----------

/** 单体对象 */
export interface MonomerObj {
  smiles: string;
  cas: string;
  name: string;
}

/** 收藏条目 */
export interface FavoriteItem {
  id: string;
  aldehyde: MonomerObj;
  amine: MonomerObj;
  latest_prediction?: { score?: number; std?: number; ood?: string } | null;
  notes?: string;
  experiment_record_ids?: string[];
}

/** 实验记录（后端落盘结构） */
export interface RecordItem {
  record_id: string;
  experiment_no: string;
  favorite_id: string | null;
  aldehyde: MonomerObj;
  amine: MonomerObj;
  prediction_snapshot?: { score?: number; std?: number; ood?: string } | null;
  conditions: Record<string, unknown>;
  outcome: 'film' | 'partial' | 'failed';
  strength: string;
  notes: string;
  operator: string;
  date: string;
  /** 仅创建响应可能携带：同收藏下编号重复警告 */
  duplicate_experiment_no?: boolean;
}

/** 创建实验记录请求体 */
export interface RecordCreateBody {
  favorite_id: string | null;
  aldehyde_smiles: string;
  amine_smiles: string;
  conditions: Record<string, string>;
  outcome: string;
  strength: string;
  notes: string;
  operator: string;
  experiment_no: string;
}

// ---------- 端点 ----------

/** 收藏列表 */
export async function listFavorites(): Promise<FavoriteItem[]> {
  const data = await request<{ favorites: FavoriteItem[] }>('/favorites');
  return data.favorites ?? [];
}

/** 实验记录列表（可选按收藏过滤） */
export async function listRecords(favoriteId?: string): Promise<RecordItem[]> {
  const qs = favoriteId ? `?favorite_id=${encodeURIComponent(favoriteId)}` : '';
  const data = await request<{ records: RecordItem[] }>(`/records${qs}`);
  return data.records ?? [];
}

/** 创建实验记录 */
export function createRecord(body: RecordCreateBody): Promise<RecordItem> {
  return request<RecordItem>('/records', { method: 'POST', body });
}

/** 删除实验记录 */
export function deleteRecord(recordId: string): Promise<void> {
  return request<void>(`/records/${encodeURIComponent(recordId)}`, { method: 'DELETE' });
}
