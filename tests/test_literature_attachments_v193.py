"""文献附件（v1.9.3 问题 2）测试：主文 + 多份 SI 上传/留存/复用/多源合并解析。

覆盖：
- 多文件上传（角色 main/si 自动分配）；sha1 内容去重不重复占盘；
- 非 PDF / 空文件 / 超限 / 超过单文献附件数上限的错误语义；
- 附件列表 / 下载 / 改角色 / 删除；
- use_stored=true 复用已存附件解析；主文与 SI 分别解析后合并去重，
  条目带 source_file 与 evidence 前缀（可核对证据出处）；
- 扫描件（无文本层）只给 422 明确提示；text 模式不受影响。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from literature import attachments, knowledge, llm_extract  # noqa: E402
from literature import graph_ingest  # noqa: E402
from references import titles  # noqa: E402

from api.main import app  # noqa: E402

client = TestClient(app)

TFPT = "O=Cc1ccc(-c2nc(-c3ccc(C=O)cc3)nc(-c3ccc(C=O)cc3)n2)cc1"
B5 = "Nc1ccc(C(F)(F)F)cc1-c1ccc(N)cc1C(F)(F)F"


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(attachments, "PDFS_DIR", tmp_path / "literature" / "pdfs")
    monkeypatch.setattr(attachments, "INDEX_PATH",
                        tmp_path / "literature" / "pdfs_index.jsonl")
    monkeypatch.setattr(knowledge, "ENTRIES_PATH",
                        tmp_path / "literature" / "knowledge_entries.jsonl")
    monkeypatch.setattr(llm_extract, "SETTINGS_PATH",
                        tmp_path / "config" / "lit_llm.json")
    monkeypatch.setattr(graph_ingest, "_app_root", lambda: tmp_path)
    patched = []
    for mod_name in ("references.titles", "src.references.titles"):
        mod = sys.modules.get(mod_name)
        if mod is not None and mod not in patched:
            monkeypatch.setattr(mod, "TITLES_PATH", tmp_path / "paper_titles.json")
            patched.append(mod)
    (tmp_path / "paper_titles.json").write_text(json.dumps({
        "1": {"title": "TFPT 膜文献", "doi": "10.1000/tfpt"},
    }, ensure_ascii=False), encoding="utf-8")
    for mod in patched:
        mod.reload()
    yield tmp_path
    for mod in patched:
        mod.reload()


def _pdf(text: str = "", pages: int = 1) -> bytes:
    """构造小 PDF；每页文本补足到 ≥60 字符（否则会被判为无文本层扫描件）。"""
    import fitz
    doc = fitz.open()
    body = (text + " " + "COF characterization data for parsing. " * 4).strip() \
        if text else ""
    for i in range(pages):
        page = doc.new_page()
        if body:
            page.insert_text((72, 72), f"{body} (page {i + 1})")
    data = doc.tobytes()
    doc.close()
    return data


MAIN_TEXT = "Main paper about COF film"
SI_TEXT = "Supporting information with PXRD data"


def _upload(name: str, data: bytes, role: str | None = None):
    files = [("files", (name, data, "application/pdf"))]
    form = {}
    if role:
        form["role"] = role
    return client.post("/api/literature/1/attachments", files=files, data=form)


# ---------------------------------------------------------------- 上传/去重

def test_upload_multiple_files_assigns_main_and_si(isolate):
    r = client.post(
        "/api/literature/1/attachments",
        files=[("files", ("main.pdf", _pdf(MAIN_TEXT), "application/pdf")),
               ("files", ("si1.pdf", _pdf(SI_TEXT), "application/pdf")),
               ("files", ("si2.pdf", _pdf(SI_TEXT + " 2"), "application/pdf"))],
    )
    assert r.status_code == 201
    body = r.json()
    assert body["count"] == 3 and not body["errors"]
    roles = [u["role"] for u in body["uploaded"]]
    assert roles == ["main", "si", "si"]        # 首份自动主文，其余 SI
    assert all(u["chars"] > 0 for u in body["uploaded"])
    listed = client.get("/api/literature/1/attachments").json()
    assert listed["count"] == 3
    assert listed["attachments"][0]["role"] == "main"   # 主文排在前面


def test_upload_same_content_is_deduplicated(isolate):
    data = _pdf(MAIN_TEXT)
    first = _upload("main.pdf", data).json()["uploaded"][0]
    second = _upload("main-copy.pdf", data).json()["uploaded"][0]
    assert second["deduplicated"] is True
    assert second["file_id"] == first["file_id"]
    assert client.get("/api/literature/1/attachments").json()["count"] == 1


def test_upload_rejects_non_pdf_and_empty(isolate):
    r = client.post("/api/literature/1/attachments",
                    files=[("files", ("a.txt", b"hello", "text/plain"))])
    assert r.status_code == 400 and "PDF" in r.json()["detail"]
    r2 = client.post("/api/literature/1/attachments",
                     files=[("files", ("empty.pdf", b"", "application/pdf"))])
    assert r2.status_code == 400


def test_upload_rejects_oversize(isolate, monkeypatch):
    monkeypatch.setattr(attachments, "MAX_PDF_BYTES", 100)
    r = _upload("big.pdf", b"%PDF" + b"x" * 500)
    assert r.status_code == 400 and "上限" in r.json()["detail"]


def test_upload_enforces_max_per_paper(isolate, monkeypatch):
    monkeypatch.setattr(attachments, "MAX_PDFS_PER_PAPER", 2)
    _upload("a.pdf", _pdf("A"))
    _upload("b.pdf", _pdf("B"))
    r = client.post("/api/literature/1/attachments",
                    files=[("files", ("c.pdf", _pdf("C"), "application/pdf")),
                           ("files", ("d.pdf", _pdf("D"), "application/pdf"))])
    # 两份都超限 → 全部失败并给出 400；已存 2 份不受影响
    assert r.status_code == 400
    assert "上限" in r.json()["detail"]
    assert client.get("/api/literature/1/attachments").json()["count"] == 2


def test_upload_role_override_and_missing_paper(isolate):
    r = _upload("si.pdf", _pdf(SI_TEXT), role="si")
    assert r.status_code == 201
    assert r.json()["uploaded"][0]["role"] == "si"
    r2 = client.post("/api/literature/999/attachments",
                     files=[("files", ("a.pdf", _pdf("A"), "application/pdf"))])
    assert r2.status_code == 404


# ---------------------------------------------------------------- 下载/改/删

def test_download_switch_role_and_delete(isolate):
    fid = _upload("main.pdf", _pdf(MAIN_TEXT)).json()["uploaded"][0]["file_id"]
    dl = client.get(f"/api/literature/attachments/{fid}/file")
    assert dl.status_code == 200
    assert dl.headers["content-type"] == "application/pdf"
    assert dl.content.startswith(b"%PDF")

    sw = client.patch(f"/api/literature/attachments/{fid}", data={"role": "si"})
    assert sw.status_code == 200 and sw.json()["role"] == "si"
    assert client.patch(f"/api/literature/attachments/{fid}",
                        data={"role": "bad"}).status_code == 400

    assert client.delete(f"/api/literature/attachments/{fid}").status_code == 200
    assert client.get(f"/api/literature/attachments/{fid}/file").status_code == 404
    assert client.delete(f"/api/literature/attachments/{fid}").status_code == 404


# ---------------------------------------------------------------- 多源解析

@pytest.fixture()
def fake_llm(monkeypatch):
    """按文本内容返回不同条目，模拟主文/SI 各自提取。"""
    def fake_parse(text):
        entries = []
        if "Main paper" in text:
            entries.append({
                "group_id": "G1", "kind": "film_outcome",
                "ald_smiles": TFPT, "amine_smiles": B5, "film_label": 1.0,
                "evidence": "主文：壁上形成连续薄膜",
            })
        if "Supporting information" in text:
            entries.append({
                "group_id": "G1", "kind": "characterization",
                "technique": "PXRD", "metrics": [{"name": "2theta", "value": 3.5}],
                "evidence": "SI：PXRD 3.5 度强峰",
            })
            entries.append({
                "group_id": "S1", "kind": "characterization",
                "technique": "BET", "metrics": [{"name": "surface_area", "value": 1200}],
                "evidence": "SI：BET 比表面 1200",
            })
        return {"llm_used": True, "entries": entries, "note": "ok",
                "segments": {"total": 1, "failed": 0}}
    monkeypatch.setattr(llm_extract, "parse_text", fake_parse)
    monkeypatch.setattr(llm_extract, "extract_paper_meta",
                        lambda text: {"llm_used": True, "meta": {"title": "T"},
                                      "note": "meta ok"})


def test_parse_uses_stored_attachments_and_merges(isolate, fake_llm):
    _upload("main.pdf", _pdf(MAIN_TEXT))
    _upload("si1.pdf", _pdf(SI_TEXT))
    r = client.post("/api/literature/1/parse", data={"use_stored": "true",
                                                     "with_meta": "true"})
    assert r.status_code == 200
    body = r.json()
    assert body["llm_used"] is True
    kinds = sorted(e["kind"] for e in body["entries"])
    assert kinds == ["characterization", "characterization", "film_outcome"]
    labels = {e["source_file"] for e in body["entries"]}
    assert any(l.startswith("[主文") for l in labels)
    assert any(l.startswith("[SI 1") for l in labels)
    # 证据前缀便于核对出处
    si_entry = next(e for e in body["entries"] if e["kind"] == "characterization")
    assert si_entry["evidence"].startswith("[SI 1")
    assert len(body["sources"]) == 2
    assert body["paper_meta"] == {"title": "T"}


def test_parse_multipart_upload_in_one_shot(isolate, fake_llm):
    r = client.post(
        "/api/literature/1/parse",
        files=[("files", ("main.pdf", _pdf(MAIN_TEXT), "application/pdf")),
               ("files", ("si1.pdf", _pdf(SI_TEXT), "application/pdf"))],
        data={"with_meta": "false"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["entries"]) == 3
    assert len(body["saved"]) == 2                    # 自动留存，供下次复用
    assert "合并后 3 条" in body["note"]
    assert client.get("/api/literature/1/attachments").json()["count"] == 2


def test_parse_store_files_false_keeps_nothing(isolate, fake_llm):
    r = client.post(
        "/api/literature/1/parse",
        files=[("files", ("main.pdf", _pdf(MAIN_TEXT), "application/pdf"))],
        data={"with_meta": "false", "store_files": "false"},
    )
    assert r.status_code == 200
    assert r.json()["saved"] == []
    assert client.get("/api/literature/1/attachments").json()["count"] == 0


def test_parse_scanned_attachment_returns_422(isolate, fake_llm):
    _upload("scan.pdf", _pdf(""))          # 空白页：无文本层
    r = client.post("/api/literature/1/parse", data={"use_stored": "true"})
    assert r.status_code == 422
    assert "文本层" in r.json()["detail"]


def test_parse_without_files_or_text_returns_400(isolate, fake_llm):
    r = client.post("/api/literature/1/parse", data={})
    assert r.status_code == 400
    assert "附件" in r.json()["detail"]


def test_parse_text_mode_still_works(isolate, fake_llm):
    r = client.post("/api/literature/1/parse", data={"text": MAIN_TEXT})
    assert r.status_code == 200
    assert r.json()["entries"][0]["kind"] == "film_outcome"


def test_source_file_survives_entry_validation():
    rec = knowledge.validate_entry({
        "kind": "film_outcome", "group_id": "G1",
        "ald_smiles": TFPT, "amine_smiles": B5, "film_label": 1.0,
        "evidence": "[SI 1 si.pdf] PXRD 数据", "source_file": "[SI 1 si.pdf]",
    })
    assert rec["source_file"] == "[SI 1 si.pdf]"


# ------------------------------------------------- 条目试校验（v1.9.3 实测发现）

@pytest.mark.parametrize("raw,expected", [
    # 规范名（含小写混合）必须原样通过 —— 旧实现 upper() 后比对导致全部被拒
    ("PXRD", "PXRD"), ("pxrd", "PXRD"), ("UVVis", "UVVis"), ("uvvis", "UVVis"),
    ("contact_angle", "contact_angle"), ("dft", "dft"),
    ("separation_flux", "separation_flux"),
    ("separation_selectivity", "separation_selectivity"),
    ("mechanical", "mechanical"), ("photocatalysis", "photocatalysis"),
    ("electrochem", "electrochem"),
    # 常见别名
    ("XRD", "PXRD"), ("IR", "FTIR"), ("ATR-FTIR", "FTIR"),
    ("UV-Vis", "UVVis"), ("DSC", "TGA"), ("FE-SEM", "SEM"),
    ("N2 adsorption", "BET"), ("water contact angle", "contact_angle"),
    ("water flux", "separation_flux"), ("rejection", "separation_selectivity"),
    # 无法识别
    ("RAMAN", ""), ("", ""),
])
def test_normalize_technique(raw, expected):
    assert knowledge.normalize_technique(raw) == expected


def test_characterization_qualitative_and_alias_validation():
    """定性表征（有 conclusion 无 metrics）可入库；别名 technique 可入库。"""
    rec = knowledge.validate_entry({
        "kind": "characterization", "group_id": "G1", "technique": "XRD",
        "sample": "膜", "metrics": [], "conclusion": "出现 3.5° 强峰，结晶性好",
        "evidence": "PXRD 显示 3.5° 强峰",
    })
    assert rec["technique"] == "PXRD" and rec["qualitative"] is True
    assert rec["metrics"] == []
    # 既无 metrics 也无 conclusion → 拒绝
    with pytest.raises(ValueError, match="metrics"):
        knowledge.validate_entry({
            "kind": "characterization", "group_id": "G1",
            "technique": "PXRD", "evidence": "x"})
    # 未知 technique → 拒绝（错误信息给出别名提示）
    with pytest.raises(ValueError, match="别名"):
        knowledge.validate_entry({
            "kind": "characterization", "group_id": "G1", "technique": "RAMAN",
            "metrics": [{"name": "shift", "value": 1000}], "evidence": "x"})


def test_annotate_entries_flags_invalid_with_reason():
    """LLM 常输出 SMILES 为空的 monomer_pair → 标 invalid 而不是让整批入库失败。"""
    rows = knowledge.annotate_entries([
        {"kind": "film_outcome", "group_id": "G1", "ald_smiles": TFPT,
         "amine_smiles": B5, "film_label": 1.0, "evidence": "成膜"},
        {"kind": "monomer_pair", "group_id": "G1", "ald_smiles": "",
         "amine_smiles": "", "evidence": "文中未给结构"},
        {"kind": "characterization", "group_id": "G2", "technique": "PXRD",
         "metrics": [{"name": "2theta", "value": 3.5}], "evidence": "峰位"},
    ])
    assert [r["valid"] for r in rows] == [True, False, True]
    assert "ald_smiles" in rows[1]["invalid_reason"]
    assert rows[0]["invalid_reason"] == ""


def test_add_entries_error_reports_index_and_kind():
    with pytest.raises(ValueError) as exc:
        knowledge.add_entries("1", [
            {"kind": "condition", "group_id": "G1", "evidence": "条件",
             "conditions": {"solvent": "toluene"}},
            {"kind": "monomer_pair", "group_id": "G1", "evidence": "缺 SMILES"},
        ])
    msg = str(exc.value)
    assert "第 2 条" in msg and "monomer_pair" in msg


def test_parse_response_annotates_validity(isolate, monkeypatch):
    """解析预览带 valid/invalid_reason 与计数，前端据此默认只勾合法条目。"""
    def fake_parse(text):
        return {"llm_used": True, "segments": {"total": 1, "failed": 0},
                "note": "ok", "entries": [
                    {"group_id": "G1", "kind": "film_outcome",
                     "ald_smiles": TFPT, "amine_smiles": B5, "film_label": 1.0,
                     "evidence": "主文：成膜"},
                    {"group_id": "G1", "kind": "monomer_pair",
                     "ald_smiles": "", "amine_smiles": "",
                     "evidence": "主文：未给出结构"},
                ]}
    monkeypatch.setattr(llm_extract, "parse_text", fake_parse)
    monkeypatch.setattr(llm_extract, "extract_paper_meta",
                        lambda text: {"llm_used": True, "meta": {}, "note": ""})
    r = client.post("/api/literature/1/parse",
                    data={"text": "正文", "with_meta": "false"})
    assert r.status_code == 200
    body = r.json()
    assert body["valid_count"] == 1 and body["invalid_count"] == 1
    valid = [e for e in body["entries"] if e["valid"]]
    invalid = [e for e in body["entries"] if e["valid"] is False]
    assert len(valid) == 1 and len(invalid) == 1
    assert "ald_smiles" in invalid[0]["invalid_reason"]
    # 只提交合法条目 → 入库成功且入图
    ok = client.post("/api/literature/1/entries",
                     json={"entries": valid})
    assert ok.status_code == 201 and ok.json()["count"] == 1
    assert ok.json()["graph_synced"] >= 1
    # 混入非法条目 → 400 且提示到具体条目
    bad = client.post("/api/literature/1/entries",
                      json={"entries": valid + invalid})
    assert bad.status_code == 400
    assert "第 2 条" in str(bad.json()["detail"])
