"""指标名同义词归一化（v1.9.8）。

## 为什么需要

实测我们的真实数据：`entry_metrics` 里的指标名以**英文**为主
（`Langmuir surface area`、`pore size`、`N-H stretching peak`…），
而用户查询习惯是中文（"比表面积"）→ `by_metric 比表面积` 命中 0，能力形同虚设。

这里建立**中文规范名 ↔ 英文/缩写写法**的双向映射，检索时把查询词展开成同义词组，
中英互查都能命中。刻意保持"轻"：一张表 + 一个纯函数，无外部依赖，便于扩展。
"""

from __future__ import annotations

# 规范中文名 → 同义写法（中/英/缩写）
METRIC_ALIASES: dict[str, list[str]] = {
    "比表面积": ["surface area", "BET surface area", "Langmuir surface area",
               "specific surface area", "SSA", "BET area"],
    "孔径": ["pore size", "pore diameter", "pore width", "micropore size",
           "mesopore size"],
    "孔容": ["pore volume", "total pore volume", "micropore volume"],
    "接触角": ["contact angle", "water contact angle", "WCA"],
    "结晶度": ["crystallinity", "crystallinity index", "CrI"],
    "产率": ["yield", "conversion", "productivity"],
    "选择性": ["selectivity", "permselectivity", "separation factor"],
    "渗透通量": ["permeance", "permeability", "flux", "water flux", "permeation flux"],
    "截留率": ["rejection", "retention", "rejection rate"],
    "热稳定性": ["thermal stability", "decomposition temperature", "Td", "TGA onset"],
    "带隙": ["band gap", "bandgap", "Eg", "optical gap"],
    "峰位": ["peak", "2theta", "2θ", "diffraction angle", "peak position"],
    "吸附量": ["uptake", "adsorption capacity", "adsorption uptake", "capacity"],
    "降解率": ["degradation", "degradation efficiency", "removal efficiency",
             "degradation rate"],
    "电导率": ["conductivity", "ionic conductivity", "proton conductivity"],
    "荧光量子产率": ["quantum yield", "PLQY", "fluorescence quantum yield"],
    "粒径": ["particle size", "diameter", "size distribution"],
    "厚度": ["thickness", "film thickness"],
    "粗糙度": ["roughness", "Ra", "RMS roughness"],
    "稳定性": ["stability", "chemical stability", "hydrolytic stability"],
}

# 常见缩写（单独给出，便于直接命中）
ABBREVIATIONS: dict[str, str] = {
    "ssa": "比表面积", "bet": "比表面积", "wca": "接触角", "plqy": "荧光量子产率",
    "eg": "带隙", "td": "热稳定性",
}


def expand_metric_name(name: str) -> list[str]:
    """把查询词展开成同义词组（**双向**，含原词本身，去重且保序）。

    规则：
    - 命中任一规范名或别名（互为子串，大小写不敏感）→ 该组全部展开；
    - 输入是缩写（如 `SSA`）→ 展开其规范名的整组；
    - 空输入 → 返回 [""]（调用方据此退化为不筛）。
    """
    q = (name or "").strip()
    if not q:
        return [""]
    ql = q.lower()
    outs: list[str] = [q]

    canonical_from_abbr = ABBREVIATIONS.get(ql)
    for canonical, aliases in METRIC_ALIASES.items():
        pool = [canonical, *aliases]
        hit = canonical == canonical_from_abbr or any(
            ql in p.lower() or p.lower() in ql for p in pool)
        if hit:
            outs.extend(pool)

    seen: set[str] = set()
    uniq: list[str] = []
    for t in outs:
        k = t.lower()
        if k and k not in seen:
            seen.add(k)
            uniq.append(t)
    return uniq
