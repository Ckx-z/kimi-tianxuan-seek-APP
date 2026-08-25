"""query_dft 工具：DFT 结合能计算（读缓存/历史 → 未命中则提交任务并轮询）。

DFT 2.0 口径：计算对象是醛/胺缩合二聚体与第三物质 X；助手工具语义固定为
「二聚体自身堆积」（self_stack），返回文案按二聚体口径表述。

- 读路径（无需确认）：缓存（dft_cache）或历史（dft_log）已有该单体对
  同方法档位的完成结果 → 直接返回；
- 写路径（需二次确认，见 confirm_impact）：未命中 → jobs.create_job 提交
  后台计算并轮询：gfnff 秒级（上限 20s），gfn2 上限 60s；超时返回
  "已提交任务 job_id 可稍后查看"（非错误）。
结果文案统一标注"半经验方法仅供相对比较"（与领域纪律一致）。
"""

from __future__ import annotations

import time

try:
    from src.dft import cache as dft_cache
    from src.dft import engine, jobs
    from src.dft import log as dft_log
except ImportError:  # pragma: no cover
    from dft import cache as dft_cache  # type: ignore
    from dft import engine, jobs  # type: ignore
    from dft import log as dft_log  # type: ignore

_POLL_INTERVAL_SEC = 1.0
_POLL_TIMEOUT = {"gfnff": 20.0, "gfn2": 60.0}

_SEMI_NOTE = ("⚠️ 以上来自 GFN-FF / GFN2-xTB 半经验方法，仅供相对比较，"
              "不能当作高精度 DFT 或实验值。")


def _fmt_num(v, fmt: str) -> str:
    return fmt.format(float(v)) if isinstance(v, (int, float)) else "（无）"


def _fmt_result(res: dict, source: str) -> str:
    lines = [f"DFT 结合能结果（{source}，方法 {res.get('method') or '?'}）："]
    # DFT 2.0 二聚体口径：结果含 dimer_smiles/x_description 时按新口径表述；
    # 旧版历史/缓存缺字段时降级标注，不臆造 X 描述
    x_desc = res.get("x_description")
    if res.get("dimer_smiles") or x_desc:
        lines.append(f"- 计算对象：缩合二聚体与 X（{x_desc or '未保存 X 描述'}）")
    else:
        lines.append("- 计算对象：缩合二聚体与 X"
                     "（旧版记录，实际为两单体结合能口径）")
    lines += [
        f"- 结合能：{_fmt_num(res.get('e_bind_kcal'), '{:.2f}')} kcal/mol"
        f"（{_fmt_num(res.get('e_bind_kj'), '{:.1f}')} kJ/mol）",
        f"- HOMO-LUMO 带隙：{_fmt_num(res.get('gap_ev'), '{:.2f}')} eV；"
        f"偶极矩：{_fmt_num(res.get('dipole_debye'), '{:.2f}')} D"]
    if isinstance(res.get("elapsed_sec"), (int, float)):
        lines.append(f"- 耗时：{float(res['elapsed_sec']):.1f} s")
    fav = res.get("favorite")
    if isinstance(fav, dict) and fav.get("id"):
        lines.append(f"- 关联收藏：{fav.get('id')}（{fav.get('folder_name') or ''}）")
    lines.append(_SEMI_NOTE)
    return "\n".join(lines)


def _lookup_done(canon_a: str, canon_b: str, method: str) -> dict | None:
    """读路径：先缓存，后历史（新→旧第一条 done）。命中返回结果 dict。

    DFT 2.0 起缓存 key 为（二聚体 SMILES, X 描述, 方法）；助手工具的
    语义等价于「醛/胺单体 → 二聚体 → 自身堆积」，故按 self_stack 查。
    二聚体生成失败（非醛胺体系）时跳过缓存直接查历史。
    """
    try:
        from src.dft import dimer as _dimer_mod
    except ImportError:  # pragma: no cover
        from dft import dimer as _dimer_mod  # type: ignore
    try:
        dim = _dimer_mod.make_dimer(canon_a, canon_b)
        hit = dft_cache.load_cache(
            dft_cache.cache_key(dim["smiles"], "self_stack", method))
    except Exception:
        hit = None
    if hit is not None:
        out = dict(hit)
        out["cached"] = True
        out["_source"] = "命中计算缓存"
        return out
    try:
        entries, _ = dft_log.read_history(limit=200)
    except Exception:
        entries = []
    want = sorted([canon_a, canon_b])
    for e in entries:
        if e.get("status") != "done" or e.get("method") != method:
            continue
        pair = sorted([str(e.get("smiles_a") or ""), str(e.get("smiles_b") or "")])
        if pair == want:
            out = dict(e)
            out["cached"] = True
            out["_source"] = "来自计算历史"
            return out
    return None


def _prepare(smiles_a: str, smiles_b: str, method: str):
    """校验 + 规范化。返回 (canon_a, canon_b, method, error_dict|None)。"""
    a, b = (smiles_a or "").strip(), (smiles_b or "").strip()
    if not a or not b:
        return None, None, method, {
            "text": "参数缺失：smiles_a 与 smiles_b 均不能为空",
            "details": {}, "is_error": True}
    method = (method or "gfn2").strip()
    if method not in engine.METHODS:
        return None, None, method, {
            "text": f"未知方法档位：{method}（可选 gfnff / gfn2）",
            "details": {}, "is_error": True}
    canon_a, canon_b = engine.canonicalize_smiles(a), engine.canonicalize_smiles(b)
    if not canon_a or not canon_b:
        return None, None, method, {
            "text": "SMILES 无法解析，请检查单体结构写法。",
            "details": {}, "is_error": True}
    # DFT 2.0：计算对象是缩合二聚体，先校验醛/胺能生成二聚体
    try:
        from src.dft import dimer as _dimer_mod
    except ImportError:  # pragma: no cover
        from dft import dimer as _dimer_mod  # type: ignore
    try:
        _dimer_mod.make_dimer(canon_a, canon_b)
    except _dimer_mod.DimerError as exc:
        return None, None, method, {
            "text": f"二聚体生成失败：{exc}",
            "details": {}, "is_error": True}
    return canon_a, canon_b, method, None


def confirm_impact(args: dict) -> str | None:
    """动态确认：缓存/历史命中（纯读）→ None 不确认；需提交计算 → 影响说明。

    SMILES 非法 / 引擎缺失时也不确认（工具会直接报错，无可确认的写操作）。
    """
    args = args if isinstance(args, dict) else {}
    try:
        canon_a, canon_b, method, err = _prepare(
            args.get("smiles_a") or "", args.get("smiles_b") or "",
            args.get("method") or "gfn2")
        if err is not None:
            return None
        if _lookup_done(canon_a, canon_b, method) is not None:
            return None
        if engine.xtb_binary() is None:
            return None
        label = engine.METHODS[method]["label"]
        budget = int(_POLL_TIMEOUT.get(method, 60.0))
        return (f"缓存与历史均无该组合结果，将提交 {label} 计算任务并等待"
                f"（最长约 {budget} 秒，超时则转后台，可稍后查看任务进度）。")
    except Exception:
        return "将提交 DFT 计算任务。"


def query_dft(smiles_a: str, smiles_b: str, method: str = "gfn2") -> dict:
    """查 DFT 结合能：缓存/历史命中直接返回；否则提交任务并轮询。"""
    try:
        canon_a, canon_b, method, err = _prepare(smiles_a, smiles_b, method)
        if err is not None:
            return err

        hit = _lookup_done(canon_a, canon_b, method)
        if hit is not None:
            return {"text": _fmt_result(hit, hit.pop("_source", "历史结果")),
                    "details": {"cached": True, "method": method,
                                "e_bind_kcal": hit.get("e_bind_kcal")},
                    "is_error": False}

        if engine.xtb_binary() is None:
            return {"text": "未安装计算引擎：未找到 xtb 二进制"
                            "（vendor/xtb/bin/xtb.exe），DFT 计算暂不可用。",
                    "details": {}, "is_error": True}

        job = jobs.create_job(canon_a, canon_b, method)
        job_id = job["job_id"]
        if job.get("status") == "done" and job.get("result"):
            # 极端竞态：提交瞬间命中缓存
            return {"text": _fmt_result(job["result"], "命中计算缓存"),
                    "details": {"cached": True, "job_id": job_id,
                                "method": method},
                    "is_error": False}

        timeout = _POLL_TIMEOUT.get(method, 60.0)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(_POLL_INTERVAL_SEC)
            cur = jobs.get_job(job_id)
            if cur is None:
                break
            if cur.get("status") == "done" and cur.get("result"):
                return {"text": _fmt_result(cur["result"], "本次计算完成"),
                        "details": {"cached": False, "job_id": job_id,
                                    "method": method,
                                    "e_bind_kcal":
                                        (cur["result"] or {}).get("e_bind_kcal")},
                        "is_error": False}
            if cur.get("status") == "failed":
                return {"text": f"DFT 计算失败：{cur.get('error') or '未知原因'}",
                        "details": {"job_id": job_id, "method": method},
                        "is_error": True}

        return {"text": f"计算已提交（任务 {job_id}，{engine.METHODS[method]['label']}），"
                        f"等待超过 {int(timeout)} 秒仍未完成，已转后台继续。"
                        f"可稍后让我「查看 DFT 任务 {job_id}」，或到 DFT 页查看进度。",
                "details": {"job_id": job_id, "status": "running",
                            "method": method},
                "is_error": False}
    except Exception as exc:
        return {"text": f"DFT 查询失败：{type(exc).__name__}: {exc}",
                "details": {}, "is_error": True}
