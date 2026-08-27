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

/** 文献引用条目（auto-matched 结构；GET 响应已经后端 enrichment：
 *  编号引用解析出真实标题 + doi + url + paper_id） */
export interface ReferenceItem {
  title?: string;
  doi?: string;
  /** 后端 enrichment 补的 DOI 链接（https://doi.org/...；无 DOI 为 null） */
  url?: string | null;
  /** 后端 enrichment 补的文献库编号（编号引用可解析时存在） */
  paper_id?: string;
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

/** DFT 快照（P3 起由 DFT 页写入：方法/结合能/能隙/偶极/计算时间；2.0 起含二聚体与 X） */
export interface DftSnapshot {
  method?: string;
  /** 缩合二聚体 SMILES（2.0 口径；旧版 v1.0.0 快照缺省 = 两单体口径） */
  dimer_smiles?: string;
  dimer_multi_site?: boolean;
  dimer_note?: string | null;
  /** X 类型（self_stack / solvent / other_dimer / custom） */
  x_type?: string;
  /** X 的中文描述（如「自身堆积（二聚体·二聚体）」「溶剂：甲苯」） */
  x_description?: string;
  e_bind_kcal?: number;
  e_bind_kj?: number;
  gap_ev?: { a?: number | null; b?: number | null; complex?: number | null };
  dipole_debye?: { a?: number | null; b?: number | null; complex?: number | null };
  date?: string;
  [key: string]: unknown;
}

/** DFT 分条记录（dft_entries 数组条目；POST /favorites/{id}/dft-entries 追加） */
export interface DftEntryItem {
  job_id?: string;
  x_type?: string;
  x_smiles?: string;
  x_description?: string;
  dimer_smiles?: string;
  /** 后端预渲染的二聚体 SVG（缺失时前端按 dimer_smiles 走 structure.svg 兜底） */
  dimer_svg?: string;
  method?: string;
  e_bind_kcal?: number;
  e_bind_kj?: number;
  created_at?: string;
  [key: string]: unknown;
}

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
  /** DFT 分条记录（新口径，读取一律以此为准） */
  dft_entries?: DftEntryItem[];
  /** 旧兼容字段：响应中回填为最新一条 dft_entries；仅作兜底 */
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

/** 复制收藏到指定收藏夹（201 返回完整新收藏；实验记录归属不随复制转移） */
export async function copyFavorite(id: string, folderId: string): Promise<FavoriteItem> {
  return request<FavoriteItem>(`/favorites/${encodeURIComponent(id)}/copy`, {
    method: 'POST',
    body: JSON.stringify({ folder_id: folderId }),
  });
}

/** 追加 DFT 条目到收藏（返回完整收藏） */
export async function appendDftEntry(id: string, entry: DftEntryItem): Promise<FavoriteItem> {
  return request<FavoriteItem>(`/favorites/${encodeURIComponent(id)}/dft-entries`, {
    method: 'POST',
    body: JSON.stringify(entry),
  });
}

/** 从 content-disposition 解析下载文件名（优先 RFC 5987 filename*，中文名） */
function parseDownloadFilename(disposition: string | null, fallback: string): string {
  if (disposition) {
    const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
    if (star) {
      try {
        return decodeURIComponent(star[1]);
      } catch {
        /* 解码失败则用兜底名 */
      }
    }
    const plain = /filename="?([^";]+)"?/i.exec(disposition);
    if (plain) return plain[1];
  }
  return fallback;
}

/** 分组导出收藏实验记录（docx 字节流，触发浏览器下载） */
export async function exportRecordsBundle(favoriteIds: string[]): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/records/export-bundle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ favorite_ids: favoriteIds }),
    });
  } catch {
    const err = new BackendUnavailableError();
    toast.error(err.message);
    throw err;
  }
  if (!res.ok) {
    let message = `导出失败（${res.status}）`;
    try {
      const data = await res.json();
      if (typeof data?.detail === 'string') message = data.detail;
    } catch {
      /* 非 JSON 响应 */
    }
    toast.error(message);
    throw new Error(message);
  }
  const blob = await res.blob();
  const filename = parseDownloadFilename(
    res.headers.get('Content-Disposition'),
    `实验记录导出_${new Date().toISOString().slice(0, 10).replaceAll('-', '')}.docx`,
  );
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
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

// ---------- 文献录入（lookup → 审核 → confirm） ----------

/** 待审核文献草稿（/api/literature/lookup 与 /extract-pdf 响应的 draft/candidates 条目） */
export interface LiteratureDraft {
  title?: string;
  authors?: string[];
  journal?: string;
  year?: number | null;
  doi?: string;
  url?: string | null;
  abstract?: string | null;
  source?: string;
  /** PDF 提取通道附带的原始文件名（source=pdf-llm 时存在） */
  pdf_filename?: string;
  /** DOI 已在文献库中时为 true */
  existing?: boolean;
  existing_paper_id?: string;
}

/** confirm 201 响应 */
export interface LiteratureConfirmResult {
  paper_id: string;
  url: string | null;
  in_training: boolean;
  graphrag_indexed: boolean;
  message: string;
}

/** 录入流程专用错误：带 HTTP 状态与结构化 detail（409 含 existing_paper_id） */
export class LiteratureApiError extends Error {
  status: number;
  existingPaperId?: string;
  constructor(status: number, message: string, existingPaperId?: string) {
    super(message);
    this.name = 'LiteratureApiError';
    this.status = status;
    this.existingPaperId = existingPaperId;
  }
}

/** 录入接口统一请求：404/409/502 映射为中文提示，409 携带已存在 paper_id */
async function literatureRequest<T>(path: string, body: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch {
    const err = new BackendUnavailableError();
    toast.error(err.message);
    throw err;
  }
  if (res.ok) return (await res.json()) as T;

  let detail: unknown = null;
  try {
    detail = (await res.json())?.detail;
  } catch {
    /* 非 JSON 响应 */
  }
  const detailText = typeof detail === 'string' ? detail : '';
  if (res.status === 409) {
    const obj = (detail && typeof detail === 'object' ? detail : {}) as {
      message?: string;
      existing_paper_id?: string;
    };
    throw new LiteratureApiError(
      409,
      obj.message || '该 DOI 已存在于文献库，未重复入库',
      obj.existing_paper_id,
    );
  }
  if (res.status === 502) {
    throw new LiteratureApiError(502, 'Crossref 暂时不可达，请稍后重试');
  }
  if (res.status === 404) {
    throw new LiteratureApiError(404, detailText || 'Crossref 未找到该 DOI 对应的文献');
  }
  throw new LiteratureApiError(res.status, detailText || `请求失败（${res.status}）`);
}

/** DOI 查询：返回单个待审核草稿（404 DOI 不存在 / 502 Crossref 不可达） */
export async function lookupLiteratureByDoi(doi: string): Promise<LiteratureDraft> {
  const data = await literatureRequest<{ draft: LiteratureDraft }>('/literature/lookup', { doi });
  return data.draft;
}

/** 标题查询：返回前 3 候选草稿供选择 */
export async function lookupLiteratureByTitle(title: string): Promise<LiteratureDraft[]> {
  const data = await literatureRequest<{ candidates: LiteratureDraft[] }>(
    '/literature/lookup',
    { title },
  );
  return Array.isArray(data?.candidates) ? data.candidates : [];
}

/** 上传文献 PDF → LLM 提取元数据 → 待审核草稿（multipart；
 *  422 无文本层 / 503 未配置 LLM / 502 提取失败，错误信息按后端 detail 展示） */
export async function extractLiteratureFromPdf(file: File): Promise<LiteratureDraft> {
  const form = new FormData();
  form.append('file', file);
  let res: Response;
  try {
    res = await fetch(`${BASE}/literature/extract-pdf`, { method: 'POST', body: form });
  } catch {
    const err = new BackendUnavailableError();
    toast.error(err.message);
    throw err;
  }
  if (res.ok) {
    const data = (await res.json()) as { draft: LiteratureDraft };
    return data.draft;
  }
  let detailText = '';
  try {
    const detail = (await res.json())?.detail;
    if (typeof detail === 'string') detailText = detail;
  } catch {
    /* 非 JSON 响应 */
  }
  throw new LiteratureApiError(res.status, detailText || `PDF 提取失败（${res.status}）`);
}

/** 审核后确认入库（reviewed_by 固定 "user"；409 抛 LiteratureApiError 带 existingPaperId） */
export async function confirmLiterature(draft: {
  title: string;
  authors: string[];
  journal: string;
  year: number | null;
  doi: string;
  abstract?: string | null;
  source?: string;
}): Promise<LiteratureConfirmResult> {
  return literatureRequest<LiteratureConfirmResult>('/literature/confirm', {
    ...draft,
    reviewed_by: 'user',
  });
}
