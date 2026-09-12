"""深度研究报告链接归一化测试（修复引用跳转 404）。

背景：撰写 prompt 曾要求 `DOI: 10.xxxx（https://doi.org/...）`，全角括号被
GFM autolink 吞进 href（只修剪 ASCII 尾标点）→ 点击跳转 404/格式错误。
本测试覆盖 normalize_markdown_links 的各类输入 + save_report/load_report 的
落盘与读取归一化（历史报告脏 markdown 也能修好）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.assistant import research  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(research, "REPORTS_DIR", tmp_path / "research")
    return tmp_path


# ---------------------------------------------------------------- 归一化

def test_fullwidth_wrapped_url_is_unwrapped():
    md = "[1] 标题. 期刊, 2026. DOI: 10.1021/jacs.3c10691（https://doi.org/10.1021/jacs.3c10691）"
    out = research.normalize_markdown_links(md)
    assert "https://doi.org/10.1021/jacs.3c10691" in out
    assert "（https" not in out
    assert "jacs.3c10691）" not in out


def test_many_real_report_lines_are_cleaned():
    md = "\n".join([
        "[1] A. *Precision Chemistry*, 2023. DOI: 10.1021/prechem.3c00118"
        "（https://doi.org/10.1021/prechem.3c00118）",
        "[2] B. *JACS*, 2023. DOI: 10.1021/jacs.3c10691"
        "（https://doi.org/10.1021/jacs.3c10691）",
    ])
    out = research.normalize_markdown_links(md)
    assert out.count("（") == 0
    assert "https://doi.org/10.1021/prechem.3c00118）" not in out
    assert "https://doi.org/10.1021/jacs.3c10691）" not in out
    # 链接本体保留
    assert "https://doi.org/10.1021/prechem.3c00118" in out


def test_markdown_link_target_tail_paren_is_stripped():
    md = "见 [1](https://doi.org/10.1000/xyz）) 与 [2](https://doi.org/10.1000/abc)"
    out = research.normalize_markdown_links(md)
    assert "[1](https://doi.org/10.1000/xyz)" in out
    assert "[2](https://doi.org/10.1000/abc)" in out
    assert "）" not in out


def test_legal_balanced_parens_url_is_preserved():
    url = ("https://chem.libretexts.org/Bookshelves/Inorganic_Chemistry/"
           "Supplemental_Modules_and_Websites_(Inorganic_Chemistry)/"
           "Chemical_Reactions/Limiting_Reagents")
    md = f"[12] Limiting Reagents. LibreTexts. {url}"
    assert research.normalize_markdown_links(md) == md


def test_url_adjacent_to_chinese_text_is_cut_clean():
    md = "参见 https://doi.org/10.1000/xyz）以及后续讨论"
    out = research.normalize_markdown_links(md)
    assert "https://doi.org/10.1000/xyz" in out
    assert "https://doi.org/10.1000/xyz）" not in out


def test_ascii_trailing_punctuation_is_stripped():
    md = "参考 https://example.com/a/b.,; 与 https://example.com/c/d."
    out = research.normalize_markdown_links(md)
    assert "https://example.com/a/b " in out
    assert out.endswith("https://example.com/c/d")


def test_normalize_is_idempotent():
    md = ("[1] T. J, 2026. DOI: 10.1/a（https://doi.org/10.1/a）\n"
          "[2](https://doi.org/10.2/b）) 正文 https://doi.org/10.3/c）")
    once = research.normalize_markdown_links(md)
    assert research.normalize_markdown_links(once) == once


def test_empty_and_plain_text_untouched():
    assert research.normalize_markdown_links("") == ""
    plain = "## 结论\n本报告无外部引用。"
    assert research.normalize_markdown_links(plain) == plain


# ---------------------------------------------------------------- 落盘 / 读取

def test_save_report_normalizes_markdown():
    rec = research.save_report(
        "rpt_test000001", "问题", "标题",
        "[1] T. 2026. DOI: 10.1/a（https://doi.org/10.1/a）", [])
    assert "（https" not in rec["markdown"]
    assert "https://doi.org/10.1/a" in rec["markdown"]


def test_load_report_fixes_historical_dirty_markdown(tmp_path):
    """修复前落盘的历史报告（脏 URL）读取时应被归一化。"""
    d = research.REPORTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    dirty = "[1] T. 2026. DOI: 10.1/a（https://doi.org/10.1/a）"
    (d / "rpt_abcdef012345.json").write_text(json.dumps({
        "report_id": "rpt_abcdef012345", "question": "q", "title": "t",
        "created_at": "2026-09-01T00:00:00+08:00", "markdown": dirty,
        "refs": [], "allow_web": True, "kind": "question",
    }, ensure_ascii=False), encoding="utf-8")
    loaded = research.load_report("rpt_abcdef012345")
    assert loaded is not None
    assert "（https" not in loaded["markdown"]
    assert "https://doi.org/10.1/a" in loaded["markdown"]


def test_writer_prompt_forbids_fullwidth_wrapped_url():
    prompt = research._WRITER_PROMPT
    assert "[10.xxxx/...](https://doi.org/10.xxxx/...)" in prompt
    assert "禁止用中文括号" in prompt
