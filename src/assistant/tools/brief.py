"""get_daily_brief 工具：今日科研日报聚合（只读，V2.2 主动能力）。

回答"我今天做了什么 / 最近进展如何"类问题：聚合当日新建/更新的实验
记录、DFT 计算任务、新收藏、新录入文献。工具本身不调 LLM 生成点评
（对话里的 LLM 会基于本工具返回的事实自己组织语言）。
"""

from __future__ import annotations


def _brief_mod():
    try:
        from src.assistant import brief
    except ImportError:  # pragma: no cover
        from assistant import brief  # type: ignore
    return brief


def get_daily_brief(date: str | None = None) -> dict:
    """当日科研日报。date 可选（YYYY-MM-DD），缺省今天。"""
    try:
        brief = _brief_mod()
        data = brief.build_daily_brief(date, generate_commentary=False)
    except Exception as exc:
        return {"text": f"日报聚合失败：{type(exc).__name__}: {exc}",
                "details": {}, "is_error": True}

    lines = [f"### {data['date']} 科研日报"]
    empty = True

    if data["records_created_count"]:
        empty = False
        lines.append(f"- 新建实验记录 {data['records_created_count']} 条：")
        for r in data["records_created"]:
            bit = (f"  · {r['record_id']}（{r['monomers']}，"
                   f"结果：{r['outcome_zh']}）")
            if r.get("self_summary"):
                bit += f" 自我总结：{r['self_summary']}"
            lines.append(bit)
    if data["records_updated_count"]:
        empty = False
        ids = "、".join(str(r["record_id"]) for r in data["records_updated"])
        lines.append(f"- 更新历史记录 {data['records_updated_count']} 条：{ids}")
    if data["dft_count"]:
        empty = False
        line = f"- DFT 计算 {data['dft_count']} 个任务"
        if data["dft_best_e_bind_kcal"] is not None:
            line += (f"，最佳结合能 {data['dft_best_e_bind_kcal']} kcal/mol"
                     "（半经验方法，仅供相对比较）")
        lines.append(line)
    if data["favorites_count"]:
        empty = False
        names = "、".join(f["monomers"] for f in data["favorites"])
        lines.append(f"- 新收藏 {data['favorites_count']} 组：{names}")
    if data["literature_count"]:
        empty = False
        lines.append(f"- 新录入文献 {data['literature_count']} 篇：")
        for lit in data["literature"][:10]:
            lines.append(f"  · {lit['title']}（paper_id={lit['paper_id']}）")

    if empty:
        lines.append("当日系统内没有新的实验记录、DFT 计算、收藏或文献录入。")

    return {
        "text": "\n".join(lines),
        "details": {
            "date": data["date"],
            "records_created_count": data["records_created_count"],
            "records_updated_count": data["records_updated_count"],
            "dft_count": data["dft_count"],
            "favorites_count": data["favorites_count"],
            "literature_count": data["literature_count"],
        },
        "is_error": False,
    }
