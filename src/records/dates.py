"""实验时间派生（v1.9.3 问题 5）：实验记录时间取「时间线第一个时间点」。

背景：`records/store.create_record` 落盘的 `date` 是**录入日期**（_today()），
而用户期望列表/详情/导出显示的是**实验实际起始时间**，即实验过程时间线里
第一个时间点。时间线 `time_label` 是自由文本（实测格式混杂）：

- `2026-9-10 8:59`、`2026-8-19  11:39`（双空格）、`2026-09-10`
- `26/7/3/10:30`（YY/M/D/H:MM）
- `2026/7/3 10:30`
- `2026-8-123 11:39`（用户笔误）、`第1天`（相对标注，无绝对日期）

策略：逐条尝试解析，**非法/不可解析即跳过继续**，返回首个合法日期；
全部不可解析（或时间线为空）时调用方回退到 `date` 并标注来源为创建时间。
`date` 语义保持不变（仍为录入日期），本模块只派生只读字段。
"""

from __future__ import annotations

import re

# 顺序要紧：先四位年（YYYY-M-D），再两位年（YY/M/D），最后尾置四位年（D/M/YYYY）
_PATTERNS: list[tuple[re.Pattern, str]] = [
    # 2026-9-10 / 2026/9/10 / 2026.9.10 / 2026年9月10日（尾随数字不算，防 2026-8-123 笔误）
    (re.compile(r"(?<!\d)(?P<y>(?:19|20)\d{2})\s*[-/.年]\s*(?P<m>\d{1,2})"
                r"\s*[-/.月]\s*(?P<d>\d{1,2})(?!\d)"), "high"),
    # 26/7/3[/10:30]（两位年在前）
    (re.compile(r"(?<!\d)(?P<y>\d{2})\s*[/-]\s*(?P<m>\d{1,2})"
                r"\s*[/-]\s*(?P<d>\d{1,2})(?!\d)"), "medium"),
    # 3/7/2026 或 7/3/2026（尾置四位年，按「日/月」解释，置信度低）
    (re.compile(r"(?<!\d)(?P<d>\d{1,2})\s*[/-]\s*(?P<m>\d{1,2})"
                r"\s*[/-]\s*(?P<y>(?:19|20)\d{2})(?!\d)"), "low"),
]


def parse_time_label(label: str) -> tuple[str | None, str]:
    """单个时间标注 → (ISO 日期 | None, 置信度)。

    置信度：high（四位年）/ medium（两位年）/ low（尾置年，日月顺序存疑）/
    ""（不可解析）。
    """
    text = str(label or "").strip()
    if not text:
        return None, ""
    for pattern, confidence in _PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        try:
            year = int(m.group("y"))
            month = int(m.group("m"))
            day = int(m.group("d"))
        except (TypeError, ValueError):
            continue
        if year < 100:                      # 两位年 → 20xx
            year += 2000 if year <= 79 else 1900
        if not (1900 <= year <= 2100):
            continue
        if not (1 <= month <= 12) or not (1 <= day <= 31):
            continue          # 非法日期（如 2026-8-123 的中间态）→ 跳过继续
        return f"{year:04d}-{month:02d}-{day:02d}", confidence
    return None, ""


def first_time_point(timeline) -> tuple[str | None, str | None, str]:
    """时间线 → (首个可解析 ISO 日期 | None, 原始标注 | None, 置信度)。

    按时间线顺序（用户填写顺序）取**第一个可解析**的时间点；不可解析的条目
    跳过继续，不会因为一条笔误就整体回退。
    """
    if not isinstance(timeline, list):
        return None, None, ""
    for entry in timeline:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("time_label") or "").strip()
        iso, confidence = parse_time_label(label)
        if iso:
            return iso, label or None, confidence
    return None, None, ""


def effective_date(record: dict) -> tuple[str, str, str, str]:
    """记录 → (experiment_date, source, label, confidence)。

    - 时间线首个可解析时间点存在：source="timeline"；
    - 否则回退录入日期：source="created"（前端据此显示「录入日期」提示）。
    """
    rec = record if isinstance(record, dict) else {}
    iso, label, confidence = first_time_point(rec.get("timeline"))
    if iso:
        return iso, "timeline", label or "", confidence
    return str(rec.get("date") or ""), "created", "", ""
