"""P1 改版 App UI 辅助逻辑测试。

后端三模块（cas_lookup / predict_log / similar_cases）由任务2并行开发，
此处用 monkeypatch 桩验证 UI 逻辑；未就位路径（ImportError 降级）也覆盖。
预测器用假对象桩，不加载真实模型，保证测试快。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "app"))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import gradio_app  # noqa: E402


# ---------------------------------------------------------------------------
# 内置单体库
# ---------------------------------------------------------------------------

class TestBuiltinMonomers:
    def test_fallback_library_loads(self):
        gradio_app._MONOMER_CACHE = None  # 清缓存，避免用例间串扰
        lib = gradio_app.load_builtin_monomers()
        assert lib["aldehydes"], "应至少有一个醛单体"
        assert lib["amines"], "应至少有一个胺单体"
        labels_ald = " ".join(l for l, _ in lib["aldehydes"])
        labels_amine = " ".join(l for l, _ in lib["amines"])
        assert "TFPT" in labels_ald or "A2" in labels_ald
        assert "TAPT" in labels_amine or "TAPB" in labels_amine
        # 醛/胺分组不串：TAPT（胺）不应出现在醛组
        assert "TAPT" not in labels_ald
        # choices 的 value 是 SMILES
        assert all(isinstance(s, str) and len(s) > 3 for _, s in lib["aldehydes"])

    def test_display_name(self):
        lib = gradio_app.load_builtin_monomers()
        smiles = lib["aldehydes"][0][1]
        assert gradio_app._display_name(smiles, lib["name_by_smiles"]) != smiles
        long_unknown = "C" * 50
        assert gradio_app._display_name(long_unknown, {}).endswith("…")


# ---------------------------------------------------------------------------
# 任务2 后端封装的降级路径
# ---------------------------------------------------------------------------

class TestBackendFallbacks:
    def test_resolve_cas_module_missing(self):
        # cas_lookup 未就位时应返回人话提示而非抛异常
        info, err = gradio_app._resolve_cas("14544-47-9")
        if err and "尚未上线" in err:
            assert info is None
        # 若任务2已就位，则可能解析成功或给出未找到提示——两条路径都合法
        else:
            assert (info is None) != (err is None)

    def test_resolve_cas_empty_input(self):
        info, err = gradio_app._resolve_cas("  ")
        assert info is None and "请输入" in err

    def test_similar_cases_module_missing(self):
        cases, msg = gradio_app._find_similar_cases("O=Cc1ccccc1", "Nc1ccc(N)cc1")
        if msg and "即将上线" in msg:
            assert cases is None
        rendered = gradio_app._format_similar_cases(cases, msg)
        assert "相似成膜案例" in rendered

    def test_log_prediction_never_raises(self):
        ok = gradio_app._log_prediction({"type": "prediction", "score": 0.5})
        assert ok in (True, False)  # 模块缺失时静默 False


# ---------------------------------------------------------------------------
# CAS 填入回调（monkeypatch 桩模拟离线路径）
# ---------------------------------------------------------------------------

class TestCasFill:
    def test_cas_fill_success_aldehyde(self, monkeypatch):
        monkeypatch.setattr(
            gradio_app, "_resolve_cas",
            lambda cas: ({"smiles": "O=Cc1ccccc1", "name": "苯甲醛", "source": "builtin"}, None))
        ald_upd, amine_upd, msg = gradio_app.cas_fill("100-52-7", "醛")
        assert ald_upd["value"] == "O=Cc1ccccc1"
        assert "value" not in amine_upd  # 另一侧不动
        assert "已填入" in msg and "苯甲醛" in msg

    def test_cas_fill_success_amine(self, monkeypatch):
        monkeypatch.setattr(
            gradio_app, "_resolve_cas",
            lambda cas: ({"smiles": "Nc1ccc(N)cc1", "name": "对苯二胺", "source": "builtin"}, None))
        ald_upd, amine_upd, msg = gradio_app.cas_fill("106-50-3", "胺")
        assert amine_upd["value"] == "Nc1ccc(N)cc1"
        assert "value" not in ald_upd

    def test_cas_fill_failure_not_silent(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_resolve_cas", lambda cas: (None, "未找到该 CAS"))
        ald_upd, amine_upd, msg = gradio_app.cas_fill("000-00-0", "醛")
        assert "value" not in ald_upd and "value" not in amine_upd
        assert "⚠️" in msg and "未找到" in msg


# ---------------------------------------------------------------------------
# 批量输入解析
# ---------------------------------------------------------------------------

class TestParsePairs:
    def test_cartesian_from_multiselect(self):
        pairs, notes = gradio_app._parse_pairs(["A1s", "A2s"], ["B1s"], "", None)
        assert pairs == [("A1s", "B1s"), ("A2s", "B1s")]
        assert any("2 醛 × 1 胺" in n for n in notes)

    def test_single_side_ignored(self):
        pairs, notes = gradio_app._parse_pairs(["A1s"], [], "", None)
        assert pairs == []
        assert any("单边" in n for n in notes)

    def test_pasted_text_formats(self):
        text = "Aald1, Bami1\nAald2\tBami2\nAald3 Bami3\n# 注释行\n坏行"
        pairs, notes = gradio_app._parse_pairs([], [], text, None)
        assert pairs == [("Aald1", "Bami1"), ("Aald2", "Bami2"), ("Aald3", "Bami3")]
        assert any("跳过" in n for n in notes)

    def test_csv_upload(self, tmp_path):
        p = tmp_path / "pairs.csv"
        p.write_text("aldehyde,amine\nAald1,Bami1\nAald2,Bami2\n", encoding="utf-8")
        pairs, notes = gradio_app._parse_pairs([], [], "", str(p))
        assert pairs == [("Aald1", "Bami1"), ("Aald2", "Bami2")]

    def test_dedup_and_cap(self):
        text = "\n".join(f"A{i},B{i}" for i in range(25)) + "\nA0,B0"
        pairs, notes = gradio_app._parse_pairs([], [], text, None)
        assert len(pairs) == gradio_app.MAX_BATCH_PAIRS
        assert any("截断" in n for n in notes)


# ---------------------------------------------------------------------------
# 批量预测 / 排序 / 导出（假预测器桩）
# ---------------------------------------------------------------------------

def _fake_predict(ald, amine):
    return {
        "ald_smiles": ald, "amine_smiles": amine,
        "tree_probability": 0.7 if "good" in ald else 0.3,
        "tree_std": 0.05, "score_std": 0.05,
        "tree_model_name": "tree_v4_ens", "tree_route": "tree_v4",
        "ood": {"level": "out" if "bad" in ald else "none", "reasons": []},
        "ensemble_probability": 0.7,
    }


class _FakePredictor:
    tree_available = True

    def predict(self, ald, amine):
        return _fake_predict(ald, amine)


@pytest.fixture
def fake_predictor(monkeypatch):
    monkeypatch.setattr(gradio_app, "_get_predictor", lambda: _FakePredictor())
    monkeypatch.setattr(gradio_app, "_one_line_reason", lambda p, a, b: "某特征 推高打分")
    monkeypatch.setattr(gradio_app, "_log_prediction", lambda rec: True)


class TestBatchPredict:
    def test_batch_rows_and_score(self, fake_predictor):
        state, table, status = gradio_app.batch_predict(
            [], [], "good_ald,Bami\nbad_ald,Bami", None)
        assert len(state["rows"]) == 2
        good = next(r for r in state["rows"] if "good" in r["ald"])
        bad = next(r for r in state["rows"] if "bad" in r["ald"])
        assert good["score"] == 0.7 and bad["score"] is None  # OOD out 不出分
        assert bad["reason"] == "OOD 不适用，不出分"
        assert "完成 2 对" in status
        # 表格默认按打分降序，⛔ 排最后
        assert table[0][2] == 0.7 and table[1][2] == "⛔"
        assert table[0][5] == "✓ 池内" and table[1][5] == "⛔ 不适用"

    def test_empty_input_status(self, fake_predictor):
        state, table, status = gradio_app.batch_predict([], [], "", None)
        assert state["rows"] == [] and table == []
        assert "没有可预测的单体对" in status

    def test_sort_and_filter(self, fake_predictor):
        state, _, _ = gradio_app.batch_predict(
            [], [], "good_ald,Bami\nbad_ald,Bami\nplain_ald,Bami", None)
        rows = state["rows"]
        # 升序
        t = gradio_app._render_batch_rows(rows, "按打分升序", "全部")
        assert t[0][2] == 0.3
        # 隐藏 ⛔
        t = gradio_app._render_batch_rows(rows, "按打分降序", "隐藏 ⛔ 不适用")
        assert all(r[2] != "⛔" for r in t)
        # 仅看池内
        t = gradio_app._render_batch_rows(rows, "按打分降序", "仅看 ✓ 池内")
        assert all(r[5] == "✓ 池内" for r in t)

    def test_export_csv(self, fake_predictor, tmp_path, monkeypatch):
        monkeypatch.setattr(gradio_app, "BATCH_EXPORT_DIR", tmp_path)
        state, _, _ = gradio_app.batch_predict([], [], "good_ald,Bami", None)
        path = gradio_app.export_batch_csv(state)
        assert path and Path(path).exists()
        content = Path(path).read_text(encoding="utf-8-sig")
        assert "成膜打分（倾向性）" in content and "good_ald" in content
        assert gradio_app.export_batch_csv({"rows": []}) is None


# ---------------------------------------------------------------------------
# 单组预测回调（假预测器桩）与 App 构建
# ---------------------------------------------------------------------------

class TestSinglePredict:
    def test_predict_outputs(self, fake_predictor, monkeypatch):
        monkeypatch.setattr(gradio_app, "_explain_tree_score", lambda p, a, b: "### 打分理由")
        monkeypatch.setattr(gradio_app, "_structure_images",
                            lambda a, b: (None, None, None, ""))
        monkeypatch.setattr(gradio_app, "_find_similar_cases",
                            lambda a, b, top_k=3: ([{"score": 0.8, "paper_id": "P1",
                                                     "similarity": 0.9}], None))
        out = gradio_app.predict("good_ald", "Bami")
        prob_text, cond_text, _, explain, _, _, _, _, similar = out
        assert "0.700" in prob_text and "成膜打分（倾向性）" in prob_text
        assert "±0.050" in prob_text  # 大字号区与明细均有 std
        assert "推荐实验条件" in cond_text
        assert "打分理由" in explain
        assert "P1" in similar and "0.90" in similar

    def test_predict_ood_out_no_score(self, fake_predictor, monkeypatch):
        monkeypatch.setattr(gradio_app, "_structure_images",
                            lambda a, b: (None, None, None, ""))
        monkeypatch.setattr(gradio_app, "_find_similar_cases",
                            lambda a, b, top_k=3: (None, "占位"))
        out = gradio_app.predict("bad_ald", "Bami")
        prob_text, _, _, explain = out[0], out[1], out[2], out[3]
        assert "模型不适用" in prob_text
        assert "0.300" not in prob_text  # ⛔ 不出分
        assert "⛔" in explain

    def test_predict_empty_input(self):
        out = gradio_app.predict("  ", "")
        assert "请先填写" in out[0]

    def test_create_app_builds(self):
        app = gradio_app.create_app()
        assert app is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
