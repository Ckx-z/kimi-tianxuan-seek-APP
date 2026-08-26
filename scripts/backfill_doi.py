"""DOI 回填脚本：为 paper_titles.json 中 doi 为空的条目按标题查 Crossref。

用法：
    python scripts/backfill_doi.py           # dry-run：打印候选与置信度，不落盘
    python scripts/backfill_doi.py --write   # 落盘回填 + 审计流水

规则：
- 每条按 title 查 Crossref（query.bibliographic + rows=3），候选按
  difflib.SequenceMatcher 标题相似度排序，取最高且 > 0.85 者回填；
- 每次请求间隔 1s（礼貌池之外再加节流）；
- 网络失败 / 低置信度列入「需人工」清单，最后统一打印；
- --write 时：回填 doi 字段 + 加 doi_backfilled:true，审计行进
  data/literature_intake.jsonl，paper_titles.json 原子落盘。
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from literature import crossref, resolver  # noqa: E402
from references import titles  # noqa: E402

THRESHOLD = 0.85
ROWS = 3
SLEEP_S = 1.0


def similarity(a: str, b: str) -> float:
    """标题相似度：difflib.SequenceMatcher（小写、压缩空白后比较）。"""
    norm = lambda s: " ".join(str(s or "").lower().split())
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def find_candidates(title: str) -> list[tuple[float, dict]]:
    """查 Crossref 并按标题相似度降序返回 [(score, draft)]。

    并列（相似度差 ≤0.005）时优先主文 DOI：ACS 等出版商的 .s001/.s002
    后缀 DOI 指向补充材料（SI），不应作为文献主 DOI 回填。
    """
    drafts = crossref.search_by_title(title, rows=ROWS)
    scored = [(similarity(title, d.get("title") or ""), d) for d in drafts]
    si_re = re.compile(r"\.s\d+$", re.IGNORECASE)
    scored.sort(key=lambda x: (x[0], not si_re.search(x[1].get("doi") or "")),
                reverse=True)
    if scored:
        best = scored[0][0]
        top = [c for c in scored if best - c[0] <= 0.005]
        top.sort(key=lambda x: bool(si_re.search(x[1].get("doi") or "")))
        scored = top + [c for c in scored if best - c[0] > 0.005]
    return scored


def main() -> int:
    ap = argparse.ArgumentParser(description="paper_titles.json DOI 回填")
    ap.add_argument("--write", action="store_true",
                    help="落盘回填（默认 dry-run 只打印）")
    args = ap.parse_args()

    papers = dict(titles._load())
    empty = [(pid, e) for pid, e in papers.items()
             if isinstance(e, dict) and not resolver.normalize_doi(e.get("doi"))]
    total = len(papers)
    print(f"文献库共 {total} 条，doi 空缺 {len(empty)} 条"
          f"（{'--write 落盘模式' if args.write else 'dry-run，不落盘'}）\n")

    backfilled: list[tuple[str, dict, float]] = []   # (pid, draft, score)
    manual: list[tuple[str, str, str]] = []          # (pid, title, reason)

    for i, (pid, entry) in enumerate(empty, 1):
        title = str(entry.get("title") or "").strip()
        if not title:
            manual.append((pid, "", "无标题，无法检索"))
            print(f"[{i}/{len(empty)}] #{pid} 无标题 → 需人工")
            continue
        try:
            cands = find_candidates(title)
        except crossref.CrossrefError as exc:
            manual.append((pid, title, f"Crossref 查询失败：{exc}"))
            print(f"[{i}/{len(empty)}] #{pid} {title[:60]}")
            print(f"    ✗ 查询失败：{exc} → 需人工")
            time.sleep(SLEEP_S)
            continue
        time.sleep(SLEEP_S)
        print(f"[{i}/{len(empty)}] #{pid} {title[:60]}")
        if not cands:
            manual.append((pid, title, "Crossref 无候选"))
            print("    ✗ 无候选 → 需人工")
            continue
        for rank, (score, d) in enumerate(cands, 1):
            mark = "★" if rank == 1 else " "
            print(f"    {mark}{rank}. 相似度 {score:.3f} | {d.get('doi') or '(无DOI)'}"
                  f" | {(d.get('title') or '')[:60]}")
        best_score, best = cands[0]
        if best_score > THRESHOLD and best.get("doi"):
            backfilled.append((pid, best, best_score))
            print(f"    ✓ 命中（{best_score:.3f} > {THRESHOLD}）→ "
                  f"{'回填' if args.write else '将回填'} {best['doi']}")
        else:
            reason = (f"最高相似度 {best_score:.3f} ≤ {THRESHOLD}"
                      if best_score <= THRESHOLD else "最佳候选无 DOI")
            manual.append((pid, title, reason))
            print(f"    ✗ {reason} → 需人工")

    if args.write and backfilled:
        for pid, draft, score in backfilled:
            entry = dict(papers[pid])
            entry["doi"] = draft["doi"]
            entry["doi_backfilled"] = True
            papers[pid] = entry
        path = Path(titles.TITLES_PATH)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(papers, ensure_ascii=False, indent=1) + "\n",
                       encoding="utf-8")
        tmp.replace(path)
        titles.reload()
        for pid, draft, score in backfilled:
            resolver.append_intake({
                "action": "doi_backfill",
                "paper_id": pid,
                "old_doi": "",
                "new_doi": draft["doi"],
                "confidence": round(score, 4),
                "matched_title": draft.get("title") or "",
                "source": "crossref",
            })
        print(f"\n已落盘 {len(backfilled)} 条回填 + {len(backfilled)} 行审计流水"
              f"（{resolver.INTAKE_PATH}）")
    elif backfilled:
        print(f"\ndry-run：{len(backfilled)} 条可回填（加 --write 落盘）")

    remaining = len(empty) - len(backfilled) if args.write else len(empty)
    final_empty = sum(
        1 for e in papers.values()
        if isinstance(e, dict) and not resolver.normalize_doi(e.get("doi")))
    print("\n================ 回填报告 ================")
    print(f"回填成功：{len(backfilled)} 条")
    print(f"待人工：  {len(manual)} 条")
    for pid, title, reason in manual:
        print(f"  - #{pid} {title[:50]} —— {reason}")
    print(f"最终空缺：{final_empty}/{total} 条"
          f"（空缺率 {final_empty / total * 100:.1f}%）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
