/**
 * DFT 计算页本地 API 辅助（端点契约与 api/routers/dft.py 对齐，DFT 2.0）
 * 2.0 计算对象：醛/胺单体 → 缩合二聚体 D，D 与第三物质 X 的结合能。
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

export type DftBackend = 'xtb' | 'psi4';
export type DftMethod = 'gfnff' | 'gfn2' | 'wb97xd3bj_svp' | 'wb97xd3bj_svp_quick' | 'b3lyp_631gdp';
/** interrupted = 服务重启后恢复的任务（参数保留，可重新提交） */
export type DftJobStatus = 'pending' | 'running' | 'done' | 'failed' | 'interrupted';
/** 第三物质 X 类型：自身堆积（默认）/ 溶剂 / 另一组单体的二聚体 / 自定义分子 */
export type DftXType = 'self_stack' | 'solvent' | 'other_dimer' | 'custom';
/** 计算模式：dimer（默认，醛胺缩合二聚体·X）| pair（任意双分子 A···B 直接结合） */
export type DftMode = 'dimer' | 'pair';

/** 复合物 xyz 片段区间（0 基、左闭右开）：a=主体（二聚体/分子 A），b=客体（X/分子 B） */
export interface DftFragmentRanges {
  a: [number, number];
  b: [number, number];
}

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

/** 内置溶剂（GET /api/dft/solvents） */
export interface DftSolvent {
  id: string;
  name_zh: string;
  smiles: string;
}

/** 二聚体预览（GET /api/dft/dimer-preview） */
export interface DimerPreview {
  dimer_smiles: string;
  multi_site: boolean;
  note: string | null;
}

/** X 原始参数回显（历史结果借缓存任务导出时重建请求用） */
export interface DftXRequest {
  solvent_id?: string | null;
  ald2_smiles?: string | null;
  amine2_smiles?: string | null;
  custom_smiles?: string | null;
}

export interface DftResult {
  /** 计算后端：缺省（旧缓存/旧历史）视为 xtb */
  backend?: DftBackend;
  /** 计算模式：缺省（旧缓存/旧历史）视为 dimer */
  mode?: DftMode;
  /** 规范化醛/胺单体 SMILES（pair 模式为分子 A/B；收藏联动等下游兼容字段） */
  smiles_a: string;
  smiles_b: string;
  /** pair 模式为 null（不经过二聚体生成） */
  dimer_smiles: string | null;
  dimer_multi_site?: boolean;
  dimer_note?: string | null;
  /** pair 模式为 null（无第三物质概念，忽略 X 相关字段） */
  x_type: DftXType | null;
  x_smiles: string;
  /** pair 模式固定为「A···B 直接结合」 */
  x_description: string;
  x_request?: DftXRequest;
  method: DftMethod;
  method_label: string;
  e_bind_hartree: number;
  e_bind_kcal: number;
  e_bind_kj: number;
  /** dimer/x 键在 pair 模式下分别对应分子 A / 分子 B */
  energies_hartree: { dimer: number; x: number; complex: number };
  gap_ev: { dimer: number | null; x: number | null; complex: number | null };
  dipole_debye: { dimer: number | null; x: number | null; complex: number | null };
  complex_atom_count?: number;
  /** 原子计数口径（v1.5.0）：dimer=二聚体原子数，x=第三物质原子数，complex=复合物（两者之和） */
  atom_budget?: { dimer: number; x: number; complex: number } | null;
  complex_xyz: string;
  fragment_ranges?: DftFragmentRanges | null;
  elapsed_sec: number;
  cached: boolean;
  favorite: DftFavoriteInfo | null;
  /** Psi4 精度档附加信息（backend=psi4 时存在） */
  psi4_detail?: DftPsi4Detail | null;
}

/** Psi4 精度档附加信息（方法/基组/BSSE 口径/fchk） */
export interface DftPsi4Detail {
  method: string;
  basis: string;
  bsse_type: string;
  psi4_version?: string | null;
  /** 未做 BSSE 校正的参考结合能（kcal/mol） */
  e_bind_raw_kcal?: number | null;
  fchk_available?: boolean;
  fchk_path?: string | null;
}

/** 任务输入参数（GET /jobs/{id} 的 input 字段，返回页面时据此恢复表单） */
export interface DftJobInput {
  ald_smiles?: string | null;
  amine_smiles?: string | null;
  x_type?: DftXType | null;
  solvent_id?: string | null;
  ald2_smiles?: string | null;
  amine2_smiles?: string | null;
  custom_smiles?: string | null;
  n_samples?: number | null;
}

export interface DftJob {
  job_id: string;
  status: DftJobStatus;
  progress_hint: string;
  /** 0-100 进度（v1.5.0：构象生成 0-20 / 优化 20-50 / 单点 50-80 / 汇总 80-100） */
  progress_percent?: number;
  method: DftMethod;
  mode?: DftMode;
  backend?: DftBackend;
  cached: boolean;
  result: DftResult | null;
  error: string | null;
  created_at?: string;
  /** 原始输入参数（后端透出，供表单恢复） */
  input?: DftJobInput | null;
}

/** 创建任务请求体（POST /jobs；旧字段 smiles_a/smiles_b 由后端兼容映射） */
export interface DftJobRequest {
  /** 缺省 dimer；pair 时 ald/amine 字段位复用为分子 A/B，忽略 x_type 相关字段 */
  mode?: DftMode;
  ald_smiles: string;
  amine_smiles: string;
  x_type?: DftXType;
  solvent_id?: string;
  ald2_smiles?: string;
  amine2_smiles?: string;
  custom_smiles?: string;
  method: DftMethod;
  /** 缺省 xtb；psi4 为真 DFT 精度档（分钟级，需已安装 psi4-env） */
  backend?: DftBackend;
  /** 复合物取向 MC 采样数（缺省后端默认 12；1=旧单取向口径；仅 gfn2/psi4 生效） */
  n_samples?: number;
  /** 仅 psi4：是否 Psi4 全几何优化（缺省 false——单点 CP 提速） */
  optimize?: boolean;
  /** 仅 psi4：并行线程数（缺省=后端配置/环境变量/4） */
  threads?: number;
}

/** 历史条目（dft_log.jsonl；2.0 起含二聚体与 X 字段，旧条目可能缺失） */
export interface DftHistoryEntry {
  timestamp?: string;
  mode?: DftMode;
  /** 缺省（旧条目）视为 xtb */
  backend?: DftBackend;
  smiles_a: string;
  smiles_b: string;
  dimer_smiles?: string | null;
  dimer_multi_site?: boolean;
  dimer_note?: string | null;
  x_type?: DftXType | null;
  x_smiles?: string;
  x_description?: string;
  x_request?: DftXRequest;
  method: DftMethod;
  /** 后端记录的方法中文标签（新条目；旧条目由前端按 method 推断） */
  method_label?: string | null;
  status: 'done' | 'failed';
  error?: string;
  e_bind_kcal?: number;
  e_bind_kj?: number;
  gap_ev?: DftResult['gap_ev'];
  dipole_debye?: DftResult['dipole_debye'];
  energies_hartree?: DftResult['energies_hartree'];
  complex_xyz?: string;
  fragment_ranges?: DftFragmentRanges | null;
  elapsed_sec?: number;
}

// ---------- 后端可用状态（GET /backends） ----------

export interface DftBackendMethodOption {
  id: string;
  label: string;
  /** Psi4 档位的 preset 名（precision/literature）；xtb 无此字段 */
  preset?: string | null;
}

export interface DftBackendInfo {
  installed: boolean;
  version: string | null;
  path: string | null;
  label: string;
  methods: DftBackendMethodOption[];
  /** 仅 psi4：默认方法档 / 安装引导 */
  default_method?: string;
  install_hint?: string | null;
  reason?: string;
}

export interface DftBackendsResponse {
  backends: { xtb: DftBackendInfo; psi4: DftBackendInfo };
}

/** 方法中文标签：优先用后端记录的 method_label，否则按 backend+method 推断 */
export function dftMethodLabel(backend: DftBackend | undefined, method: string, recorded?: string | null): string {
  if (recorded) return recorded;
  if (method === 'b3lyp_631gdp') return 'B3LYP/6-31G(d,p)（文献口径）';
  if (method === 'wb97xd3bj_svp_quick') return 'ωB97X-D3BJ/def2-SV(P)（批量快速档）';
  if (backend === 'psi4' || method === 'wb97xd3bj_svp') return 'ωB97X-D3BJ/def2-SVP（真 DFT）';
  return method === 'gfnff' ? 'GFN-FF 力场（快速）' : 'GFN2-xTB（精确）';
}

// ---------- 端点 ----------

/** 计算页表单草稿（后端落盘，切页/刷新后恢复；结构由前端定义，后端原样存取） */
export interface DftDraft {
  mode?: DftMode;
  monoA?: { smiles: string; name: string };
  monoB?: { smiles: string; name: string };
  xType?: DftXType;
  solventId?: string;
  monoA2?: { smiles: string; name: string };
  monoB2?: { smiles: string; name: string };
  customSmiles?: string;
  method?: DftMethod;
  backend?: DftBackend;
  psi4Method?: DftMethod;
  currentJobId?: string | null;
}

/** 创建计算任务（202；缓存命中时返回的 job 直接 done 且 cached=true） */
export const createDftJob = (req: DftJobRequest) =>
  request<DftJob>('/jobs', { method: 'POST', body: req });

/** 轮询任务状态（静默：轮询期间失败由页面统一处理，不每跳弹 toast） */
export const fetchDftJob = (jobId: string) =>
  request<DftJob>(`/jobs/${encodeURIComponent(jobId)}`, { silent: true });

/** 内置溶剂表（x_type=solvent 下拉选项） */
export const fetchDftSolvents = async (): Promise<DftSolvent[]> => {
  const data = await request<{ solvents: DftSolvent[] }>('/solvents', { silent: true });
  return data.solvents ?? [];
};

/** 各计算后端可用状态（静默：选择器初始化与安装后轮询检测用） */
export const fetchDftBackends = async (): Promise<DftBackendsResponse['backends']> => {
  const data = await request<DftBackendsResponse>('/backends', { silent: true });
  return data.backends;
};

/** 二聚体预览：醛/胺单体 → 缩合二聚体 SMILES + 多位点标注 */
export const fetchDimerPreview = (aldSmiles: string, amineSmiles: string) =>
  request<DimerPreview>(
    `/dimer-preview?ald_smiles=${encodeURIComponent(aldSmiles)}&amine_smiles=${encodeURIComponent(amineSmiles)}`,
    { silent: true },
  );

/** 计算历史（新→旧） */
export const fetchDftHistory = async (limit = 50): Promise<DftHistoryEntry[]> => {
  const data = await request<{ history: DftHistoryEntry[] }>(`/history?limit=${limit}`, { silent: true });
  return data.history ?? [];
};

/** 读计算页草稿（静默：页面初始化用，失败由页面兜底） */
export const fetchDftDraft = () =>
  request<{ draft: DftDraft | null }>('/draft', { silent: true });

/** 保存计算页草稿（静默：防抖自动保存，失败不打扰用户） */
export const saveDftDraft = (draft: DftDraft) =>
  request<{ ok: boolean }>('/draft', { method: 'PUT', body: { draft }, silent: true });

// ---------- 导出量化软件输入文件（GET /jobs/{id}/export） ----------

export type DftExportFormat = 'gaussian' | 'orca';

/** 从 content-disposition 解析下载文件名（优先 RFC 5987 filename*，中文名） */
function parseDownloadFilename(disposition: string | null, fallback: string): string {
  if (disposition) {
    const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
    if (star) {
      try {
        return decodeURIComponent(star[1]);
      } catch {
        // 解码失败则用兜底名
      }
    }
    const plain = /filename="?([^";]+)"?/i.exec(disposition);
    if (plain) return plain[1];
  }
  return fallback;
}

/**
 * 导出 Gaussian(.gjf) / ORCA(.inp) 输入文件并触发浏览器下载。
 * jobId 缺省（历史回显等无任务场景）时先按结果的 X 原始参数重建任务——
 * 缓存命中会立即 done，仅借其 job_id 调后端导出端点，保证导出格式单一来源（后端生成）。
 */
export async function exportDftInput(
  result: DftResult,
  format: DftExportFormat,
  jobId?: string | null,
): Promise<void> {
  let id = jobId || null;
  if (!id) {
    const job = await createDftJob({
      mode: result.mode ?? 'dimer',
      ald_smiles: result.smiles_a,
      amine_smiles: result.smiles_b,
      x_type: result.x_type ?? undefined,
      solvent_id: result.x_request?.solvent_id ?? undefined,
      ald2_smiles: result.x_request?.ald2_smiles ?? undefined,
      amine2_smiles: result.x_request?.amine2_smiles ?? undefined,
      custom_smiles: result.x_request?.custom_smiles ?? undefined,
      method: result.method,
      backend: result.backend ?? 'xtb',
    });
    id = job.job_id;
  }
  let res: Response;
  try {
    res = await fetch(`${BASE}/jobs/${encodeURIComponent(id)}/export?format=${format}`);
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
      // 保留默认提示
    }
    toast.error(message);
    throw new Error(message);
  }
  const text = await res.text();
  const filename = parseDownloadFilename(
    res.headers.get('Content-Disposition'),
    format === 'gaussian' ? 'dft_input.gjf' : 'dft_input.inp',
  );
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** 把 DFT 结果快照合并写入已有收藏（PATCH dft_snapshot） */
export function mergeDftToFavorite(favoriteId: string, result: DftResult) {
  return requestFavorite(favoriteId, buildDftSnapshot(result));
}

/** 由计算结果构造 dft_snapshot（落盘字段，「我的」页可展示；2.0 起含二聚体与 X） */
export function buildDftSnapshot(result: DftResult) {
  return {
    backend: result.backend ?? 'xtb',
    method: result.method,
    method_label: result.method_label,
    dimer_smiles: result.dimer_smiles,
    dimer_multi_site: result.dimer_multi_site ?? false,
    dimer_note: result.dimer_note ?? null,
    x_type: result.x_type,
    x_smiles: result.x_smiles,
    x_description: result.x_description,
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
  /** 目标收藏夹 id（可选，缺省归入默认夹） */
  folder_id?: string;
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
