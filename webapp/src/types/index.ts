/**
 * 后端 API 响应类型定义（与 FastAPI 后端契约对应）
 * 波次 2 各页面统一从 '@/types' 引用
 */

// ---------- 通用 ----------
export interface ApiError {
  detail?: string;
  message?: string;
}

// ---------- /api/predict ----------
export interface PredictRequest {
  aldehyde: string;
  amine: string;
  [key: string]: unknown;
}

/** OOD（分布外）评估 */
export interface OodInfo {
  level: string; // 如 "in" | "warn" | "out"
  reasons: string[];
}

export interface PredictResponse {
  score: number;
  score_policy: string;
  tree_score: number;
  gnn_score: number;
  tree_std: number;
  ood: OodInfo;
  [key: string]: unknown;
}

// ---------- /api/monomers ----------
export interface Monomer {
  name: string;
  smiles?: string;
  type?: string; // aldehyde / amine
  [key: string]: unknown;
}

// ---------- /api/records 实验记录 ----------
export interface ExperimentRecord {
  record_id: string;
  experiment_no: string;
  conditions: Record<string, unknown>;
  outcome: Record<string, unknown>;
  /** 录入日期（创建记录当天） */
  date: string;
  /** v1.9.3 派生：实验起始时间（时间线首个时间点；取不到时 = date） */
  experiment_date?: string;
  date_source?: 'timeline' | 'created';
  date_label?: string;
  [key: string]: unknown;
}

// ---------- /api/favorites 收藏 ----------
export interface Favorite {
  id: string;
  aldehyde: string;
  amine: string;
  latest_prediction?: PredictResponse | null;
  references?: string[];
  experiment_record_ids?: string[];
  [key: string]: unknown;
}

// ---------- /api/iterate 方案迭代 ----------
export interface SuggestionPayload {
  title: string;
  confidence: number; // 0~1
  [key: string]: unknown;
}

export interface Suggestion {
  suggestion_id: string;
  type: string;
  payload: SuggestionPayload;
  batch: string;
  status: string; // pending / accepted / rejected ...
  created_at: string;
  [key: string]: unknown;
}

export interface Plan {
  plan_id: string;
  seq: number;
  template_name: string;
  plan_card?: PlanCard | null;
  [key: string]: unknown;
}

// ---------- /api/plan-card 与 /api/plan-templates ----------
export interface PlanCard {
  title?: string;
  steps?: string[];
  [key: string]: unknown;
}

export interface PlanTemplate {
  name: string;
  description?: string;
  [key: string]: unknown;
}

// ---------- /api/health ----------
export interface HealthResponse {
  status: string; // "ok" 等
  [key: string]: unknown;
}

// ---------- /api/llm ----------
export interface LlmChatRequest {
  message: string;
  context?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface LlmChatResponse {
  reply: string;
  [key: string]: unknown;
}
