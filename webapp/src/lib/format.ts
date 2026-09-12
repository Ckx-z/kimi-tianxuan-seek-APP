/**
 * 展示层格式化工具（v1.9.3 UI）。
 *
 * 背景：后端多处直接返回 ISO 时间串（如 `2026-08-19T08:52:00+08:00`），界面
 * 直接渲染会露出 `T` 与时区后缀，既不美观也不统一。这里集中处理，避免各页
 * 各写一遍 `replace('T',' ').slice(...)`。
 */

/** ISO → `YYYY-MM-DD HH:mm`（缺失返回空串；非法串原样返回，便于排查） */
export function formatDateTime(value: unknown): string {
  const s = String(value ?? '').trim();
  if (!s) return '';
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/);
  if (m) return `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}`;
  const d = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return d ? `${d[1]}-${d[2]}-${d[3]}` : s;
}

/** ISO → `YYYY-MM-DD`（缺失返回空串） */
export function formatDate(value: unknown): string {
  const s = String(value ?? '').trim();
  if (!s) return '';
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[1]}-${m[2]}-${m[3]}` : s;
}

/**
 * 置信度归一化（v1.9.3）：后端 `payload.confidence` 实际是
 * `{level: 'high'|'medium'|'low', reason}` 对象；早期前端按数字 `*100` 处理，
 * 于是界面出现「置信度 NaN%」。这里兼容 数字 / `{score|value}` / `{level}`。
 */
export function confidenceLevel(confidence: unknown): 'high' | 'medium' | 'low' | '' {
  if (typeof confidence === 'number' && Number.isFinite(confidence)) {
    return confidence >= 0.8 ? 'high' : confidence >= 0.5 ? 'medium' : 'low';
  }
  if (confidence && typeof confidence === 'object') {
    const obj = confidence as { score?: unknown; value?: unknown; level?: unknown };
    for (const key of ['score', 'value'] as const) {
      const v = obj[key];
      if (typeof v === 'number' && Number.isFinite(v)) {
        return v >= 0.8 ? 'high' : v >= 0.5 ? 'medium' : 'low';
      }
    }
    const level = String(obj.level ?? '').toLowerCase();
    if (level === 'high' || level === 'medium' || level === 'low') return level;
  }
  return '';
}
