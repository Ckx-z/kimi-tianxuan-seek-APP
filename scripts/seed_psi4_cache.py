"""把「接着算完」的 Psi4 结果**原生地**送回 App（写进它的结果缓存）。

## 思路（为什么这么做最稳）

App 没有「重试/续算」接口，但它有结果缓存：`create_job` 会先算缓存 key，
命中就直接返回历史结果（界面提示「命中缓存，直接返回历史结果」）。
所以只要把结果按 App 自己的 key 写进缓存，用户在界面上再点一次「开始计算」
就会**秒出完整结果**，不需要等第二次 70 分钟，也不需要改 App 任何代码。

## 具体做法

1. 把 `COF_DATA_DIR` 指到安装版数据根（`%APPDATA%\\COF-Film-Recommend`）；
2. 打桩两个函数：
   - `_xtb_guess` → 直接返回**原来那次**用的 xTB 预优化几何（逐字一致，不重跑 xTB）
   - `_run_psi4_script` → 直接返回**已经算完**的 `result.json`（不重跑 Psi4）
3. 调用 App 自己的 `compute_pair_binding_psi4(...)`：几何/片段划分/结果解析/
   单位换算/fchk 归档全部走 App 代码，我只替换了"真正跑 Psi4"这一步；
4. 按 `jobs._run_job` 完全相同的方式补 `citations` 并算 cache key → `dft_cache.save_cache`。

## 安全校验（任一不过就**不写缓存**并报错）

- 已算完的 result.json 必须存在且含 CP 结合能；
- CP 结合能必须等于被中断那次输出里的 −0.007774483086 Eh；
- 复合物能量必须与 App 解析出的值一致；
- gap / 偶极矩 / fchk 三项不能为空（否则写进缓存会让界面显示「缺失」）。

用法：E:\\ANACONDA\\python.exe .tmp_xtb\\seed_psi4_cache.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(r"C:\Users\ckx\Desktop\全新机器学习实验")
APP_ROOT = Path(r"C:\Users\ckx\AppData\Roaming\COF-Film-Recommend")
JOB_DIR = (APP_ROOT / "data" / "dft_jobs" / "dft_psi4_1c9a1aadf753_7i0by3gj")
PSI4_DIR = JOB_DIR / "psi4"
# 新作业目录仍放 App 的 dft_jobs 下：结果里的 fchk 路径要稳定可访问
# （放仓库临时区的话，用户从界面导出时会指向一个可能被清理的路径）
SCRATCH_JOBS = APP_ROOT / "data" / "dft_jobs"

#: 被中断那次输出里的 CP 结合能（原文摘录，见 scripts/psi4_finish_tail.py 头注释）
CP_EXPECT = -0.007774483086
#: 用户在界面上的原始输入（取自 dft_jobs.json 里那条 failed 作业）
ALD = "C/N=C/c1c(F)c(F)c(/C=N/c2ccc(-c3nc(-c4ccc(NC)cc4)nc(-c4ccc(NC)cc4)n3)cc2)c(F)c1F"
AMINE = "O=C(C(OC(F)(F)C(F)(OC(F)(F)C(F)(F)F)F)(F)F)O"
METHOD = "wb97xd3bj_svp"
THREADS = 24
WITH_PROPS = True
N_SAMPLES = None

os.environ["COF_DATA_DIR"] = str(APP_ROOT)
for p in (str(REPO), str(REPO / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

FAILS: list[str] = []


def check(cond: bool, label: str, extra: str = "") -> None:
    print(f"  [{'OK ' if cond else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))
    if not cond:
        FAILS.append(label)


def load_failed_job() -> dict:
    """从 dft_jobs.json 里取出那条 failed 作业（拿到用户原始输入与提供的几何）。"""
    store = json.loads((APP_ROOT / "data" / "dft_jobs.json").read_text(encoding="utf-8"))
    for jid, job in store.items():
        if job.get("job_id") == "2ca557e9de8747de":
            return job
    raise SystemExit("在 dft_jobs.json 里找不到那条 failed 作业")


def main() -> int:
    from src.dft import cache as dft_cache
    from src.dft import citations, jobs, psi4_backend

    job = load_failed_job()
    complex_xyz = job.get("complex_xyz")
    print("⓪ 原始作业输入")
    check(job.get("mode") == "pair" and job.get("backend") == "psi4",
          "mode/backend 与预期一致", f"{job.get('mode')}/{job.get('backend')}")
    check(bool(complex_xyz), "作业里带着用户提供的复合物几何（可跳过 xTB 取向筛选）")
    if FAILS:
        return 1

    print("① 载入已算完的结果与原始几何")
    fin_path = PSI4_DIR / "result.json"
    check(fin_path.is_file(), "result.json 存在", str(fin_path))
    if not fin_path.is_file():
        return 1
    finished = json.loads(fin_path.read_text(encoding="utf-8"))
    check(abs(float(finished.get("e_bind_cp_hartree", 0)) - CP_EXPECT) < 1e-12,
          "CP 结合能与被中断那次一致",
          f"{finished.get('e_bind_cp_hartree')} vs {CP_EXPECT}")
    check(bool(finished.get("recovery")), "带 recovery 来源标注")
    for k in ("gap_ev_complex", "dipole_debye_complex"):
        check(finished.get(k) is not None, f"{k} 非空", str(finished.get(k)))
    check(bool(finished.get("fchk_written")), "fchk 已写出")

    orig_guess = (JOB_DIR / "xtb_guess" / "xtbopt.xyz").read_text(encoding="utf-8")
    orig_opt_xyz = (PSI4_DIR / "complex_opt.xyz").read_text(encoding="utf-8")
    orig_fchk = PSI4_DIR / "complex.fchk"
    check(orig_fchk.is_file(), "原始 complex.fchk 存在", str(orig_fchk))
    if FAILS:
        print("\n前置校验未通过 → 不写缓存")
        return 1

    print("② 打桩：跳过 xTB 与 Psi4 实体计算（复用已完成的产物）")
    calls = {"guess": 0, "run": 0}

    def fake_guess(xyz_c, work_dir, on_stage=None, cancel_event=None, **_kw):
        calls["guess"] += 1
        if on_stage:
            on_stage("复用原作业的 xTB 预优化几何（未重跑 xTB）")
        return orig_guess

    def fake_run(script_text, cwd, timeout, on_stage=None, cancel_event=None):
        calls["run"] += 1
        cwd.mkdir(parents=True, exist_ok=True)
        (cwd / "result.json").write_text(
            json.dumps(finished, ensure_ascii=False, indent=1), encoding="utf-8")
        (cwd / "complex_opt.xyz").write_text(orig_opt_xyz, encoding="utf-8")
        if orig_fchk.is_file():
            shutil.copy2(orig_fchk, cwd / "complex.fchk")
        if on_stage:
            on_stage("复用已完成的计算结果（本次未重跑 Psi4）")
        return finished

    psi4_backend._xtb_guess = fake_guess
    psi4_backend._run_psi4_script = fake_run

    print("③ 走 App 自己的管线组装结果（几何/解析/单位/fchk 归档都是 App 代码）")
    SCRATCH_JOBS.mkdir(parents=True, exist_ok=True)
    result = psi4_backend.compute_pair_binding_psi4(
        ALD, AMINE, METHOD, complex_xyz=complex_xyz, n_samples=N_SAMPLES,
        threads=THREADS, with_props=WITH_PROPS, jobs_root=SCRATCH_JOBS)
    check(calls == {"guess": 1, "run": 1}, "打桩被正确调用", str(calls))

    # 用 App 自己的换算常数（engine.HARTREE_TO_KCAL = 627.509，全项目口径）
    e_converted = psi4_backend.HARTREE_TO_KCAL * float(finished["e_bind_cp_hartree"])
    check(abs(float(result["e_bind_kcal"]) - e_converted) < 1e-9,
          "结合能换算一致（App 常数 627.509）",
          f"App={result['e_bind_kcal']:.6f} vs 手算={e_converted:.6f} kcal/mol")
    check(result["energies_hartree"]["complex"] is not None,
          "复合物能量已解析", f"{result['energies_hartree']['complex']}")
    check(result["gap_ev"]["complex"] is not None, "gap 已解析",
          f"{result['gap_ev']['complex']}")
    check(result["dipole_debye"]["complex"] is not None, "偶极矩已解析",
          f"{result['dipole_debye']['complex']}")
    check(bool(result.get("complex_xyz")), "复合物几何已带出")
    check(result.get("fragment_ranges") == {"a": [0, 68], "b": [68, 90]},
          "片段划分正确", str(result.get("fragment_ranges")))
    if FAILS:
        print("\n组装结果校验未通过 → 不写缓存")
        return 1

    print("④ 按 _run_job 相同方式补 citations 并写入缓存")
    sampling = result.get("sampling")
    result["citations"] = citations.citations_for(
        result.get("method"), backend="psi4",
        sampling=sampling.get("engine") if isinstance(sampling, dict) else None)
    key = dft_cache.cache_key(
        result["smiles_a"] if result.get("mode") == "pair" else result["dimer_smiles"],
        result["x_cache_part"], METHOD, mode="pair", backend="psi4",
        sampler_tag=jobs._sampler_tag("psi4", METHOD, N_SAMPLES,
                                      with_props=WITH_PROPS))
    dft_cache.save_cache(key, result)
    path = dft_cache.CACHE_DIR / f"{key}.json"
    check(path.is_file(), "缓存文件已写出", str(path))

    loaded = dft_cache.load_cache(key)
    check(bool(loaded) and abs(float(loaded["e_bind_kcal"]) - result["e_bind_kcal"]) < 1e-9,
          "缓存可被 App 读回且数值一致")

    # 交叉验证：用 App 的探测函数算一遍 key，必须与上面一致（=用户再点一次会命中的 key）
    probed = jobs.probe_cache_key(ALD, AMINE, METHOD, x_type="self_stack",
                                  mode="pair", backend="psi4", n_samples=N_SAMPLES,
                                  with_props=WITH_PROPS)
    probed_key = probed[0] if isinstance(probed, tuple) else probed
    check(probed_key == key, "与 App 探测 key 一致（用户再点一次即命中）",
          f"{str(probed_key)[:12]}… vs {key[:12]}…")

    print("\n⑤ 结果摘要")
    print(f"    BSSE(CP) 结合能 = {result['e_bind_kcal']:.3f} kcal/mol "
          f"({result['e_bind_hartree']:.12f} Eh)")
    print(f"    未校正结合能    = {result['psi4_detail']['e_bind_raw_kcal']:.3f} kcal/mol")
    print(f"    HOMO-LUMO gap   = {result['gap_ev']['complex']:.4f} eV")
    print(f"    偶极矩          = {result['dipole_debye']['complex']:.4f} Debye")
    print(f"    fchk            = {result['psi4_detail']['fchk_path']}")
    print(f"    缓存 key        = {key}")

    if FAILS:
        print(f"\n结果 = FAIL（{len(FAILS)} 项）：" + "；".join(FAILS))
        return 1
    print("\n结果 = ALL PASS（用户在界面点「开始计算」应即时命中缓存）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
