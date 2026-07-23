/**
 * 统一 API 客户端
 * - baseURL 使用相对路径 /api，开发环境由 vite proxy 转发到 http://localhost:8000
 * - 统一错误处理：抛出带中文 message 的 Error，并可选用中文 toast 提示（sonner）
 * - 波次 2 各页面统一通过本模块调用后端，不要直接 fetch
 */
import { toast } from 'sonner';
import type {
  ExperimentRecord,
  Favorite,
  HealthResponse,
  LlmChatRequest,
  LlmChatResponse,
  Monomer,
  Plan,
  PlanCard,
  PlanTemplate,
  PredictRequest,
  PredictResponse,
  Suggestion,
} from '@/types';

const BASE_URL = '/api';

/** 后端不可用（网络层失败）时抛出此错误，调用方可据此做优雅降级 */
export class BackendUnavailableError extends Error {
  constructor(message = '后端未连接，请确认 FastAPI 服务已启动（http://localhost:8000）') {
    super(message);
    this.name = 'BackendUnavailableError';
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  /** 出错时是否弹出中文 toast，默认 true；静默探测（如健康检查）传 false */
  silent?: boolean;
}

/** 统一 fetch 封装 */
async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, silent = false } = options;
  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, {
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
    // 尝试解析后端返回的错误详情
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

  // 204 无内容
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ---------- 健康检查 ----------
export const healthApi = {
  /** 静默探测后端是否在线（不弹 toast，失败抛 BackendUnavailableError） */
  check: () => request<HealthResponse>('/health', { silent: true }),
};

// ---------- 预测打分 ----------
export const predictApi = {
  predict: (data: PredictRequest) =>
    request<PredictResponse>('/predict', { method: 'POST', body: data }),
};

// ---------- 单体库 ----------
export const monomersApi = {
  list: async () => (await request<{ aldehydes: Monomer[]; amines: Monomer[] }>('/monomers')),
};

// ---------- 收藏 ----------
export const favoritesApi = {
  list: async () => (await request<{ favorites: Favorite[] }>('/favorites')).favorites,
  create: (data: Partial<Favorite>) =>
    request<Favorite>('/favorites', { method: 'POST', body: data }),
  remove: (id: string) => request<void>(`/favorites/${id}`, { method: 'DELETE' }),
};

// ---------- 实验记录 ----------
export const recordsApi = {
  list: async (favoriteId?: string) =>
    (await request<{ records: ExperimentRecord[] }>(
      '/records' + (favoriteId ? `?favorite_id=${encodeURIComponent(favoriteId)}` : ''))).records,
  create: (data: Partial<ExperimentRecord>) =>
    request<ExperimentRecord>('/records', { method: 'POST', body: data }),
  get: (recordId: string) => request<ExperimentRecord>(`/records/${recordId}`),
  update: (recordId: string, data: Partial<ExperimentRecord>) =>
    request<ExperimentRecord>(`/records/${recordId}`, { method: 'PUT', body: data }),
  remove: (recordId: string) => request<void>(`/records/${recordId}`, { method: 'DELETE' }),
};

// ---------- 方案卡与模板 ----------
export const planApi = {
  getCard: (planId: string) => request<PlanCard>(`/plan-card/${planId}`),
  listTemplates: async () => (await request<{ templates: PlanTemplate[] }>('/plan-templates')).templates,
};

// ---------- 方案迭代 ----------
export const iterateApi = {
  listPlans: async () => (await request<{ plans: Plan[] }>('/iterate/plans')).plans,
  createPlan: (data: Partial<Plan>) =>
    request<Plan>('/iterate/plans', { method: 'POST', body: data }),
  listSuggestions: async () => (await request<{ suggestions: Suggestion[] }>('/iterate/suggestions')).suggestions,
  updateSuggestion: (suggestionId: string, data: { status: string }) =>
    request<Suggestion>(`/iterate/suggestions/${suggestionId}`, { method: 'PATCH', body: data }),
};

// ---------- LLM ----------
export const llmApi = {
  chat: (data: LlmChatRequest) =>
    request<LlmChatResponse>('/llm/chat', { method: 'POST', body: data }),
};
