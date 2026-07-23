/**
 * 批量排序页本地 API 辅助与数据工具
 * - 不修改共享 @/lib/api，按同样的约定封装：失败弹中文 toast 并抛错
 * - 后端契约（api/routers/predict.py、api/routers/monomers.py）：
 *   GET  /api/monomers      → { aldehydes: MonomerItem[], amines: MonomerItem[] }
 *   POST /api/predict/batch → { results: BatchResultItem[], errors: BatchErrorItem[] }
 */
import { toast } from 'sonner';

// ---------- 类型 ----------
export interface MonomerItem {
  name: string;
  smiles: string;
  role?: string;
  cas?: string;
}

export interface MonomerLibrary {
  aldehydes: MonomerItem[];
  amines: MonomerItem[];
}

/** 一对待预测的醛-胺组合 */
export interface PairInput {
  ald_smiles: string;
  amine_smiles: string;
  ald_name?: string;
  amine_name?: string;
}

export interface OodInfo {
  level: string; // in | warn | out
  reasons?: string[];
}

/** 批量预测单项结果（字段与后端 build_prediction_payload 对齐） */
export interface BatchResultItem {
  ald_smiles: string;
  amine_smiles: string;
  score: number | null;
  tree_score: number | null;
  gnn_score: number | null;
  ood: OodInfo;
  [key: string]: unknown;
}

export interface BatchErrorItem {
  index: number;
  error: string;
}

export interface BatchPredictResponse {
  results: BatchResultItem[];
  errors: BatchErrorItem[];
}

// ---------- 请求封装（错误处理约定与 @/lib/api 一致） ----------
export class BackendUnavailableError extends Error {
  constructor(message = '后端未连接，请确认 FastAPI 服务已启动（http://localhost:8000）') {
    super(message);
    this.name = 'BackendUnavailableError';
  }
}

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
  return (await res.json()) as T;
}

/** 获取内置单体库（醛 / 胺分组） */
export const fetchMonomers = () => request<MonomerLibrary>('/monomers');

/** 批量预测（一次提交一批 pairs） */
export const predictBatch = (pairs: PairInput[]) =>
  request<BatchPredictResponse>('/predict/batch', {
    method: 'POST',
    body: { pairs: pairs.map((p) => ({ ald_smiles: p.ald_smiles, amine_smiles: p.amine_smiles })) },
  });

// ---------- 文本解析 ----------
/**
 * 解析粘贴文本：每行一对 SMILES，逗号 / 空白 / 制表符分隔
 * 返回 { pairs, badLines }，badLines 为无法解析的行号（1 起）
 */
export function parsePastedPairs(text: string): { pairs: PairInput[]; badLines: number[] } {
  const pairs: PairInput[] = [];
  const badLines: number[] = [];
  text.split(/\r?\n/).forEach((raw, idx) => {
    const line = raw.trim();
    if (!line) return; // 跳过空行
    const parts = line.split(/[,，\s\t]+/).filter(Boolean);
    if (parts.length >= 2) {
      pairs.push({ ald_smiles: parts[0], amine_smiles: parts[1] });
    } else {
      badLines.push(idx + 1);
    }
  });
  return { pairs, badLines };
}

/** 去重（按 醛+胺 组合键），保持先入先出顺序 */
export function dedupePairs(pairs: PairInput[]): PairInput[] {
  const seen = new Set<string>();
  return pairs.filter((p) => {
    const key = `${p.ald_smiles}|||${p.amine_smiles}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/** 分数展示：null（如 OOD=out）显示为 — */
export function fmtScore(v: number | null | undefined): string {
  return v === null || v === undefined ? '—' : v.toFixed(3);
}
