"""P1 改版 App UI 辅助逻辑测试。

后端三模块（cas_lookup / predict_log / similar_cases）由任务2并行开发，
此处用 monkeypatch 桩验证 UI 逻辑；未就位路径（ImportError 降级）也覆盖。
预测器用假对象桩，不加载真实模型，保证测试快。
"""

from __future__ import annotations

import subprocess
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
        assert "成膜打分（倾向性·较高值）" in content and "good_ald" in content
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


# ---------------------------------------------------------------------------
# P2：收藏夹 / 方案卡 / 实验记录（任务1后端一律 monkeypatch 桩，不写盘）
# ---------------------------------------------------------------------------

_FAKE_FAV = {
    "id": "fav_20260722_001",
    "aldehyde": {"smiles": "O=Cc1ccccc1", "cas": "", "name": "A2"},
    "amine": {"smiles": "Nc1ccc(N)cc1", "cas": "", "name": "TAPT"},
    "created_at": "2026-07-22T10:00:00+08:00",
    "notes": "重点组合",
    "latest_prediction": {"score": 0.699, "std": 0.04, "arm": "tree_v4",
                          "ood": "none", "date": "2026-07-22"},
    "references": [
        {"title": "101", "doi": "", "source": "auto-matched",
         "path_or_url": "", "match_type": "both", "count": 1,
         "note": "报道过该醛胺组合"},
        {"title": "手动文献", "doi": "10.1/abc", "source": "user-added",
         "path_or_url": "", "note": "支撑打分决策"},
    ],
    "experiment_record_ids": [],
}

# 任务1 plan_card 真实 schema：checklist 为 {item, detail}，conditions 英文键
_FAKE_CARD = {
    "template": "侯老师法（v3.9 方案）",
    "defaults_note": "按 v3.9 方案默认值，可按组调整",
    "conditions": {"solvent": "甲苯（或氯仿）", "catalyst": "6M 乙酸",
                   "temperature_c": 120},
    "steps": ["先醛+苯胺", "后胺", "最后乙酸"],
    "checklist": [{"item": "乙酸浓度核对", "detail": "必须用 6M 乙酸，6M≠18M"}],
    "monomer_hints": ["含氟单体注意溶解性"],
}

_FAKE_REC = {
    "record_id": "rec_20260722_001",
    "favorite_id": "fav_20260722_001",
    "aldehyde": {"smiles": "O=Cc1ccccc1", "name": "A2"},
    "amine": {"smiles": "Nc1ccc(N)cc1", "name": "TAPT"},
    "prediction_snapshot": {"score": 0.65, "std": 0.04, "ood": "none"},
    "conditions": {"solvent": "甲苯", "catalyst": "6M 乙酸",
                   "temperature_c": "120", "time_days": "3"},
    "outcome": "failed", "strength": "膜脆", "notes": "乙酸用错浓度",
    "operator": "测试员", "date": "2026-07-22",
}


class TestSnapshotPayload:
    def test_normal_pred_maps_score(self):
        payload = gradio_app._snapshot_payload(_fake_predict("good_ald", "B"))
        assert payload["score"] == 0.7 and payload["std"] == 0.05
        assert payload["arm"] == "tree_v4_ens" and payload["ood"] == "none"

    def test_ood_out_nulls_score(self):
        payload = gradio_app._snapshot_payload(_fake_predict("bad_ald", "B"))
        assert payload["score"] is None and payload["ood"] == "out"


class TestFavoriteFlow:
    def test_favorite_with_snapshot(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_fav_add",
                            lambda a, b, an, bn, n: (dict(_FAKE_FAV), None))
        snapped = {}
        monkeypatch.setattr(
            gradio_app, "_fav_update_snapshot",
            lambda fid, pred: snapped.update(fid=fid, pred=pred) or (True, None))
        monkeypatch.setattr(
            gradio_app, "_snapshot_payload", lambda pred: {"score": 0.7})
        gradio_app._LAST_PREDICTION.clear()
        gradio_app._LAST_PREDICTION.update(
            {"ald": "ALD", "amine": "AMN", "pred": {"tree_probability": 0.7}})
        msg = gradio_app.favorite_current("ALD", "AMN", "备注")
        assert "已收藏" in msg and "快照" in msg
        assert snapped["fid"] == _FAKE_FAV["id"]

    def test_favorite_without_snapshot(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_fav_add",
                            lambda a, b, an, bn, n: (dict(_FAKE_FAV), None))
        gradio_app._LAST_PREDICTION.clear()
        msg = gradio_app.favorite_current("ALD", "AMN", "")
        assert "已收藏" in msg and "重新打分" in msg

    def test_favorite_backend_missing(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_load_favorites_store",
                            lambda: (None, "⏳ 收藏夹后端模块尚未上线（后端开发中），本功能暂不可用。"))
        msg = gradio_app.favorite_current("ALD", "AMN", "")
        assert "尚未上线" in msg

    def test_favorite_empty_input(self):
        assert "请先填写" in gradio_app.favorite_current(" ", "AMN", "")


class TestPlanCard:
    def test_real_schema_render(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_plan_generate",
                            lambda a, b, an, bn, template=None: (dict(_FAKE_CARD), None))
        html_out, status = gradio_app.plan_card_for_input("O=Cc1ccccc1", "Nc1ccc(N)cc1")
        assert "✓" in status
        assert "侯老师法" in html_out and "防错清单" in html_out
        assert "6M≠18M" in html_out  # {item, detail} 拼接
        assert "溶剂" in html_out and "催化剂" in html_out  # 英文键→中文标签
        assert "含氟单体注意溶解性" in html_out

    def test_template_param_passed(self, monkeypatch):
        """P4b：模板下拉值（模板 id）经 resolve 后以 dict 传给 generate_plan_card。"""
        seen = {}
        monkeypatch.setattr(
            gradio_app, "_plan_generate",
            lambda a, b, an, bn, template=None: seen.update(template=template)
            or (dict(_FAKE_CARD), None))
        tpl = {"id": "tpl_x", "name": "自定义模板"}
        monkeypatch.setattr(gradio_app, "resolve_template_choice", lambda v: tpl)
        gradio_app.plan_card_for_input("O=Cc1ccccc1", "Nc1ccc(N)cc1", "tpl_x")
        assert seen["template"] == tpl

    def test_backend_missing(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_plan_generate",
                            lambda a, b, an, bn, template=None: (None, "⏳ 方案卡模块尚未上线（后端开发中）。"))
        html_out, status = gradio_app.plan_card_for_input("ALD", "AMN")
        assert html_out == "" and "尚未上线" in status

    def test_list_conditions_compat(self):
        card = {"conditions": [{"param": "温度", "value": "120 °C"}],
                "monomer_hints": "单条提示字符串"}
        html_out = gradio_app._render_plan_card_html(card, "A", "B")
        assert "温度" in html_out and "单条提示字符串" in html_out


class TestFavoritePages:
    def test_refresh_favorites(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_fav_list",
                            lambda: ([dict(_FAKE_FAV)], None))
        cards, sel, status = gradio_app.refresh_favorites()
        assert "共 1 条收藏" in status
        assert "fav-badge" in cards and "0.699" in cards  # 打分徽章
        assert "A2 × TAPT" in cards
        assert sel["choices"][0][1] == _FAKE_FAV["id"]

    def test_refresh_backend_missing(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_fav_list",
                            lambda: (None, "⏳ 收藏夹后端模块尚未上线"))
        cards, sel, status = gradio_app.refresh_favorites()
        assert "尚未上线" in cards and "尚未上线" in status

    def test_score_badge_semantics(self):
        assert "#0f766e" in gradio_app._score_badge_html(0.7, 0.04, "none")
        assert "#b45309" in gradio_app._score_badge_html(0.5, None, "warning")
        assert "⛔" in gradio_app._score_badge_html(None, None, "out")
        assert "未打分" in gradio_app._score_badge_html(None, None, "none")

    def test_show_detail(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_fav_get",
                            lambda fid: (dict(_FAKE_FAV), None))
        monkeypatch.setattr(gradio_app, "_rec_list",
                            lambda favorite_id=None: ([dict(_FAKE_REC)], None))
        info, snap, notes, refs, recs = gradio_app.show_favorite_detail(
            _FAKE_FAV["id"])
        assert "A2 × TAPT" in info and "fav_20260722_001" in info
        assert "0.699" in snap and "池内" in snap
        assert notes == "重点组合"
        assert "相关文献·自动匹配" in refs and "手动添加" in refs
        assert "rec_20260722_001" in recs and "预测 0.650" in recs

    def test_show_detail_missing(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_fav_get",
                            lambda fid: (None, "⚠️ 收藏条目不存在"))
        info = gradio_app.show_favorite_detail("fav_x")[0]
        assert "不存在" in info

    def test_save_notes_and_add_ref(self, monkeypatch):
        updated = {}
        monkeypatch.setattr(gradio_app, "_fav_update",
                            lambda fid, **kw: updated.update(kw) or ({}, None))
        assert "✓" in gradio_app.save_favorite_notes("fav_x", "新备注")
        assert updated["notes"] == "新备注"
        monkeypatch.setattr(gradio_app, "_fav_add_ref",
                            lambda fid, t, d, u, n: ({}, None))
        monkeypatch.setattr(gradio_app, "_fav_get",
                            lambda fid: (dict(_FAKE_FAV), None))
        st, refs = gradio_app.add_favorite_reference("fav_x", "标题", "", "", "")
        assert "✓" in st and "相关文献·自动匹配" in refs
        st, _ = gradio_app.add_favorite_reference("fav_x", " ", "", "", "")
        assert "标题不能为空" in st

    def test_auto_match_refs(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_fav_get",
                            lambda fid: (dict(_FAKE_FAV), None))
        monkeypatch.setattr(
            gradio_app, "_fav_auto_refs",
            lambda a, b: ([{"title": "202", "match_type": "aldehyde",
                            "note": "报道过该醛单体"}], None))
        refs, st = gradio_app.auto_match_favorite_refs(_FAKE_FAV["id"])
        assert "自动匹配到 1 篇" in st and "202" in refs

    def test_rescore(self, fake_predictor, monkeypatch):
        monkeypatch.setattr(gradio_app, "_fav_get",
                            lambda fid: (dict(_FAKE_FAV), None))
        snapped = {}
        monkeypatch.setattr(
            gradio_app, "_fav_update_snapshot",
            lambda fid, pred: snapped.update(fid=fid) or (True, None))
        st, snap_md = gradio_app.rescore_favorite(_FAKE_FAV["id"])
        assert "✓" in st and snapped["fid"] == _FAKE_FAV["id"]
        assert "最新预测快照" in snap_md

    def test_delete(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_fav_delete", lambda fid: (True, None))
        monkeypatch.setattr(gradio_app, "_fav_list", lambda: ([], None))
        out = gradio_app.delete_favorite(_FAKE_FAV["id"])
        assert "已删除" in out[0]


class TestRecordsPage:
    def test_submit_success(self, monkeypatch):
        created = {}
        monkeypatch.setattr(
            gradio_app, "_rec_create_linked",
            lambda fid, cond, out, s, n, op, exp="": created.update(
                fid=fid, outcome=out, cond=cond, exp=exp)
            or (dict(_FAKE_REC), None))
        monkeypatch.setattr(gradio_app, "_rec_list",
                            lambda favorite_id=None: ([dict(_FAKE_REC)], None))
        st, timeline, _, _pick, *resets = gradio_app.submit_record(
            "fav_20260722_001", "A5", "甲苯", "二氧六环", "氯仿", "",
            "6M 乙酸", "120", "3", "先醛后胺", "失败", "膜脆", "测试员", "乙酸用错")
        assert "✓" in st and "rec_20260722_001" in st and "A5" in st
        assert created["outcome"] == "failed"  # 中文→契约枚举
        assert created["exp"] == "A5"          # 实验编号透传后端
        # P4a 修复 d：conditions 新键
        assert created["cond"]["solvent_1"] == "甲苯"
        assert created["cond"]["solvent_2"] == "二氧六环"
        assert created["cond"]["eluent"] == "氯仿"
        assert "solvent" not in created["cond"]
        assert "预测 0.650 ± 0.040" in timeline
        assert "实际" in timeline and "⛔ 失败" in timeline
        # P4a 修复 c：提交成功后表单重置（17 个字段更新）
        assert len(resets) == 17

    def test_submit_requires_experiment_no(self):
        """P4a 修复 b：实验编号为独立必填字段，空则前端拦截。"""
        st, timeline, _, _pick, *resets = gradio_app.submit_record(
            "fav_x", "  ", "甲苯", "", "", "", "", "", "", "", "成膜", "", "", "")
        assert "实验编号" in st and "必填" in st and "⚠️" in st
        assert timeline == ""

    def test_submit_requires_favorite(self):
        st, _, _, *_ = gradio_app.submit_record(
            "", "A5", "甲苯", "", "", "", "", "", "", "", "成膜", "", "", "")
        assert "收藏" in st and "⚠️" in st

    def test_submit_requires_content(self):
        st, _, _, *_ = gradio_app.submit_record(
            "fav_x", "A5", "", "", "", "", "", "", "", "", "成膜", "", "", "")
        assert "至少填写一项" in st

    def test_timeline_without_snapshot(self):
        rec = dict(_FAKE_REC, prediction_snapshot=None, outcome="film")
        timeline = gradio_app._render_records_timeline([rec])
        assert "无预测快照" in timeline and "✓ 成膜" in timeline

    def test_timeline_ood_snapshot(self):
        rec = dict(_FAKE_REC, prediction_snapshot={"score": None, "std": None,
                                                   "ood": "out"})
        timeline = gradio_app._render_records_timeline([rec])
        assert "OOD 不适用" in timeline

    def test_pair_names_fallback_to_favorite(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_fav_get",
                            lambda fid: (dict(_FAKE_FAV), None))
        rec = {"record_id": "rec_x", "favorite_id": "fav_20260722_001",
               "conditions": {}, "outcome": "partial"}
        timeline = gradio_app._render_records_timeline([rec])
        assert "A2 × TAPT" in timeline and "部分成膜" in timeline

    def test_refresh_records_tab_missing(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_rec_list",
                            lambda favorite_id=None: (None, "⏳ 实验记录后端模块尚未上线"))
        _, recs_html, _pick = gradio_app.refresh_records_tab()
        assert "尚未上线" in recs_html


# ---------------------------------------------------------------------------
# 主分数口径（两模型较高值）：主展示分数 == max(树模型分, GNN 分) + 护栏标注
# ---------------------------------------------------------------------------

class TestMainScoreContract:
    """树 0.30 / GNN 0.90 / 综合 0.60 三分离：主分数必须是 0.90（较高值），
    取树会得 0.30、取综合会得 0.60——均可被断言区分。"""

    @staticmethod
    def _pred(ald, amine):
        return {
            "ald_smiles": ald, "amine_smiles": amine,
            "tree_probability": 0.30, "score_std": 0.05,
            "tree_model_name": "tree_v4_ens", "tree_route": "tree_v4",
            "gnn_probability": 0.90, "gnn_std": 0.02,
            "ensemble_probability": 0.60,
            "ood": {"level": "none", "reasons": []},
        }

    class _P:
        tree_available = True

        def predict(self, a, b):
            return TestMainScoreContract._pred(a, b)

    @pytest.fixture
    def pred_stub(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_get_predictor", lambda: self._P())
        monkeypatch.setattr(gradio_app, "_log_prediction", lambda rec: True)
        monkeypatch.setattr(gradio_app, "_explain_tree_score", lambda p, a, b: "")
        monkeypatch.setattr(gradio_app, "_structure_images",
                            lambda a, b: (None, None, None, ""))
        monkeypatch.setattr(gradio_app, "_find_similar_cases",
                            lambda a, b, top_k=3: ([], None))
        monkeypatch.setattr(gradio_app, "_one_line_reason",
                            lambda p, a, b: "某特征 推高打分")

    def _custom_stub(self, monkeypatch, pred):
        """用指定 pred 替换预测器（其余封装与 pred_stub 一致）。"""
        class _P2:
            tree_available = True

            def predict(self, a, b):
                return dict(pred)

        monkeypatch.setattr(gradio_app, "_get_predictor", lambda: _P2())
        monkeypatch.setattr(gradio_app, "_log_prediction", lambda rec: True)
        monkeypatch.setattr(gradio_app, "_explain_tree_score", lambda p, a, b: "")
        monkeypatch.setattr(gradio_app, "_structure_images",
                            lambda a, b: (None, None, None, ""))
        monkeypatch.setattr(gradio_app, "_find_similar_cases",
                            lambda a, b, top_k=3: ([], None))
        monkeypatch.setattr(gradio_app, "_one_line_reason",
                            lambda p, a, b: "某特征 推高打分")

    def test_headline_is_max_of_two_models(self, pred_stub):
        prob_text = gradio_app.predict("Aald", "Bami")[0]
        headline = prob_text.split("### 成膜打分")[0]
        assert "0.900" in headline      # 主分数 = max(树 0.30, GNN 0.90)
        assert "0.300" not in headline  # 不是树模型分
        assert "0.600" not in headline  # 不用综合分冒充
        assert "两模型较高值" in headline  # 护栏标注
        # 下方并列：树模型分（±std，含路由臂）、GNN 分、来源说明
        assert "**树模型 (tree_v4_ens)**: 0.300" in prob_text
        assert "**GNN v5.3**: 0.900" in prob_text
        assert "主分数取两模型较高者，属乐观召回口径" in prob_text
        assert "综合打分（树与 GNN 平均，仅对照参考）" in prob_text

    def test_headline_tree_higher(self, pred_stub, monkeypatch):
        """树更高时主分数取树——验证 max 双向，而非恒取 GNN。"""
        pred = self._pred("A", "B")
        pred["tree_probability"], pred["gnn_probability"] = 0.95, 0.30
        self._custom_stub(monkeypatch, pred)
        prob_text = gradio_app.predict("Aald", "Bami")[0]
        headline = prob_text.split("### 成膜打分")[0]
        assert "0.950" in headline and "两模型较高值" in headline
        assert "0.300" not in headline

    def test_batch_score_is_max(self, pred_stub):
        state, table, _ = gradio_app.batch_predict([], [], "Aald,Bami", None)
        assert state["rows"][0]["score"] == 0.90
        assert table[0][2] == 0.9

    def test_snapshot_payload_is_max_with_policy(self):
        payload = gradio_app._snapshot_payload(self._pred("A", "B"))
        assert payload["score"] == 0.90
        assert payload["score_policy"] == "max_tree_gnn"
        assert payload["tree_score"] == 0.30 and payload["gnn_score"] == 0.90

    def test_log_record_is_max_with_policy(self):
        rec = gradio_app._build_log_record("A", "B", self._pred("A", "B"), "single")
        assert rec["score"] == 0.90
        assert rec["score_policy"] == "max_tree_gnn"
        assert rec["tree_score"] == 0.30 and rec["gnn_score"] == 0.90

    def test_log_record_ood_out_null_score(self):
        """契约：ood.level=="out" 时 score 必须为 null（⛔ 优先于打分）。"""
        pred = dict(self._pred("A", "B"))
        pred["ood"] = {"level": "out", "reasons": ["双未见"]}
        rec = gradio_app._build_log_record("A", "B", pred, "single")
        assert rec["score"] is None and rec["ood_level"] == "out"

    def test_single_source_gnn_only(self, pred_stub, monkeypatch):
        """仅 GNN 出分：主分数用 GNN 并标注来源。"""
        pred = self._pred("A", "B")
        del pred["tree_probability"]
        self._custom_stub(monkeypatch, pred)
        prob_text = gradio_app.predict("Aald", "Bami")[0]
        headline = prob_text.split("### 成膜打分")[0]
        assert "0.900" in headline and "仅 GNN 出分" in headline
        assert "0.300" not in headline
        state, _, _ = gradio_app.batch_predict([], [], "Aald,Bami", None)
        assert state["rows"][0]["score"] == 0.90

    def test_single_source_tree_only(self, pred_stub, monkeypatch):
        """仅树模型出分：主分数用树并标注来源。"""
        pred = self._pred("A", "B")
        del pred["gnn_probability"]
        self._custom_stub(monkeypatch, pred)
        prob_text = gradio_app.predict("Aald", "Bami")[0]
        headline = prob_text.split("### 成膜打分")[0]
        assert "0.300" in headline and "仅树模型出分" in headline
        assert "0.900" not in headline

    def test_both_missing_no_score(self, pred_stub, monkeypatch):
        """两模型都未出分：主分数区给未出分提示，不显示任何替代分。"""
        pred = self._pred("A", "B")
        del pred["tree_probability"]
        del pred["gnn_probability"]
        self._custom_stub(monkeypatch, pred)
        prob_text = gradio_app.predict("Aald", "Bami")[0]
        headline = prob_text.split("### 成膜打分")[0]
        assert "均未出分" in headline
        assert "score-big" not in headline  # 大字号区不显示任何替代分
        state, _, _ = gradio_app.batch_predict([], [], "Aald,Bami", None)
        assert state["rows"][0]["score"] is None
        assert "两模型均未出分" in state["rows"][0]["reason"]

    def test_ood_out_no_score(self, pred_stub, monkeypatch):
        """OOD=out：⛔ 优先于打分——任何模型分都不出，主分数区为空。"""
        pred = dict(self._pred("A", "B"))
        pred["ood"] = {"level": "out", "reasons": ["双未见"]}
        self._custom_stub(monkeypatch, pred)
        prob_text = gradio_app.predict("Aald", "Bami")[0]
        assert "模型不适用" in prob_text
        assert "score-big" not in prob_text
        assert "0.900" not in prob_text and "0.300" not in prob_text
        state, _, _ = gradio_app.batch_predict([], [], "Aald,Bami", None)
        assert state["rows"][0]["score"] is None
        assert state["rows"][0]["reason"] == "OOD 不适用，不出分"


# ---------------------------------------------------------------------------
# Bug 2 回归：页③ 收藏选择 → 详情联动
# ---------------------------------------------------------------------------

class TestFavoriteSelection:
    def test_dynamic_dropdowns_allow_custom_value(self):
        """gradio 6 preprocess 按服务端 choices（恒为 []）校验选中值，
        allow_custom_value=True 才能放行动态下发的收藏 id——本断言防回退。"""
        app = gradio_app.create_app()
        dds = [c for c in app.blocks.values()
               if c.__class__.__name__ == "Dropdown"]
        fav_dd = next(c for c in dds if getattr(c, "label", "") == "选择收藏条目")
        rec_dd = next(c for c in dds
                      if str(getattr(c, "label", "")).startswith("关联收藏条目"))
        filter_dd = next(c for c in dds if getattr(c, "label", "") == "按收藏过滤")
        assert fav_dd.allow_custom_value and rec_dd.allow_custom_value
        assert filter_dd.allow_custom_value

    def test_select_to_detail_linkage(self, monkeypatch):
        """refresh 下拉的 value → show_favorite_detail 传参 → 五区详情刷新。"""
        monkeypatch.setattr(gradio_app, "_fav_list",
                            lambda: ([dict(_FAKE_FAV)], None))
        monkeypatch.setattr(
            gradio_app, "_fav_get",
            lambda fid: (dict(_FAKE_FAV), None) if fid == _FAKE_FAV["id"]
            else (None, f"⚠️ 收藏条目 {fid} 不存在（可能已被删除）。"))
        monkeypatch.setattr(gradio_app, "_rec_list",
                            lambda favorite_id=None: ([dict(_FAKE_REC)], None))
        cards, sel, status = gradio_app.refresh_favorites()
        fid = sel["choices"][0][1]
        assert fid == _FAKE_FAV["id"]
        info, snap, notes, refs, recs = gradio_app.show_favorite_detail(fid)
        assert "A2 × TAPT" in info and fid in info      # 信息区
        assert "0.699" in snap                          # 快照区
        assert notes == "重点组合"                       # 备注回填
        assert "相关文献·自动匹配" in refs                # 文献区
        assert "rec_20260722_001" in recs               # 关联记录区

    def test_select_invalid_id_friendly_error(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_fav_list", lambda: ([dict(_FAKE_FAV)], None))
        monkeypatch.setattr(
            gradio_app, "_fav_get",
            lambda fid: (None, f"⚠️ 收藏条目 {fid} 不存在（可能已被删除）。"))
        info = gradio_app.show_favorite_detail("fav_99999999_999")[0]
        assert "不存在" in info


# ---------------------------------------------------------------------------
# P3-2：收藏去重提示
# ---------------------------------------------------------------------------

class TestFavoriteDedup:
    def test_duplicate_updates_snapshot_instead_of_new(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_fav_list",
                            lambda: ([dict(_FAKE_FAV)], None))

        def _no_add(*a):
            raise AssertionError("重复收藏不应新建")

        monkeypatch.setattr(gradio_app, "_fav_add", _no_add)
        snapped = {}
        monkeypatch.setattr(
            gradio_app, "_fav_update_snapshot",
            lambda fid, pred: snapped.update(fid=fid) or (True, None))
        gradio_app._LAST_PREDICTION.clear()
        gradio_app._LAST_PREDICTION.update(
            {"ald": "O=Cc1ccccc1", "amine": "Nc1ccc(N)cc1",
             "pred": {"tree_probability": 0.7}})
        msg = gradio_app.favorite_current("O=Cc1ccccc1", "Nc1ccc(N)cc1", "")
        assert "已收藏过" in msg and _FAKE_FAV["id"] in msg
        assert "已更新快照" in msg
        assert snapped["fid"] == _FAKE_FAV["id"]

    def test_duplicate_without_snapshot(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_fav_list",
                            lambda: ([dict(_FAKE_FAV)], None))

        def _no_add(*a):
            raise AssertionError("重复收藏不应新建")

        monkeypatch.setattr(gradio_app, "_fav_add", _no_add)
        gradio_app._LAST_PREDICTION.clear()
        msg = gradio_app.favorite_current("O=Cc1ccccc1", "Nc1ccc(N)cc1", "")
        assert "已收藏过" in msg and _FAKE_FAV["id"] in msg
        assert "重新打分" in msg

    def test_swapped_pair_is_not_duplicate(self, monkeypatch):
        """醛/胺顺序颠倒不算同组合，应正常新建。"""
        monkeypatch.setattr(gradio_app, "_fav_list",
                            lambda: ([dict(_FAKE_FAV)], None))
        monkeypatch.setattr(gradio_app, "_fav_add",
                            lambda a, b, an, bn, n: (dict(_FAKE_FAV), None))
        gradio_app._LAST_PREDICTION.clear()
        msg = gradio_app.favorite_current("Nc1ccc(N)cc1", "O=Cc1ccccc1", "")
        assert "已收藏「" in msg

    def test_new_pair_still_creates(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_fav_list",
                            lambda: ([dict(_FAKE_FAV)], None))
        monkeypatch.setattr(gradio_app, "_fav_add",
                            lambda a, b, an, bn, n: (dict(_FAKE_FAV), None))
        gradio_app._LAST_PREDICTION.clear()
        msg = gradio_app.favorite_current("O=CC=C", "NC=C", "")
        assert "已收藏「" in msg and "已收藏过" not in msg


# ---------------------------------------------------------------------------
# P3-3：游离实验记录
# ---------------------------------------------------------------------------

class TestFreeRecord:
    def test_submit_free_record(self, monkeypatch):
        created = {}
        monkeypatch.setattr(
            gradio_app, "_rec_create_free",
            lambda a, b, cond, out, s, n, op, exp="": created.update(
                ald=a, amine=b, fid_checked=True, exp=exp)
            or (dict(_FAKE_REC), None))
        monkeypatch.setattr(gradio_app, "_rec_list",
                            lambda favorite_id=None: ([dict(_FAKE_REC)], None))
        st, timeline, _, _pick, *resets = gradio_app.submit_record(
            "", "G2-3", "甲苯", "", "", "", "6M 乙酸", "120", "3", "",
            "成膜", "", "测试员", "",
            True, "O=Cc1ccccc1", "Nc1ccc(N)cc1")
        assert "✓" in st
        assert created["ald"] == "O=Cc1ccccc1"
        assert created["amine"] == "Nc1ccc(N)cc1"
        assert created["exp"] == "G2-3"
        assert len(resets) == 17

    def test_free_record_requires_smiles(self):
        st, _, _, *_ = gradio_app.submit_record(
            "", "A5", "甲苯", "", "", "", "", "", "", "", "成膜", "", "", "",
            True, "", "Nc1ccc(N)cc1")
        assert "⚠️" in st and "SMILES" in st

    def test_linked_mode_unchanged(self, monkeypatch):
        created = {}
        monkeypatch.setattr(
            gradio_app, "_rec_create_linked",
            lambda fid, cond, out, s, n, op, exp="": created.update(fid=fid)
            or (dict(_FAKE_REC), None))
        monkeypatch.setattr(gradio_app, "_rec_list",
                            lambda favorite_id=None: ([], None))
        st, _, _, *_ = gradio_app.submit_record(
            "fav_20260722_001", "A5", "甲苯", "", "", "", "", "", "", "",
            "成膜", "", "", "", False, "", "")
        assert "✓" in st and created["fid"] == "fav_20260722_001"

    def test_free_wrapper_calls_pinned_signature(self, monkeypatch):
        """游离记录封装按钉死签名调 create_record（关键字传参，含 experiment_no）。"""
        calls = {}

        class _Store:
            @staticmethod
            def create_record(**kw):
                calls.update(kw)
                return {"record_id": "rec_x"}

        monkeypatch.setattr(gradio_app, "_load_records_store",
                            lambda: (_Store, None))
        rec, err = gradio_app._rec_create_free(
            "ALD", "AMN", {"solvent_1": "甲苯"}, "film", "", "备注", "操作人", "A5")
        assert err is None and rec["record_id"] == "rec_x"
        assert calls["favorite_id"] is None
        assert calls["aldehyde_smiles"] == "ALD"
        assert calls["amine_smiles"] == "AMN"
        assert calls["conditions"] == {"solvent_1": "甲苯"}
        assert calls["outcome"] == "film"
        assert calls["notes"] == "备注" and calls["operator"] == "操作人"
        assert calls["experiment_no"] == "A5"

    def test_wrapper_typeerror_fallback_merges_exp_no(self, monkeypatch):
        """后端未支持 experiment_no（TypeError）时降级：编号并入 notes 前缀。"""
        calls = {}

        class _OldStore:
            @staticmethod
            def create_record(**kw):
                if "experiment_no" in kw:
                    raise TypeError("unexpected keyword")
                calls.update(kw)
                return {"record_id": "rec_y"}

        monkeypatch.setattr(gradio_app, "_load_records_store",
                            lambda: (_OldStore, None))
        rec, err = gradio_app._rec_create_linked(
            "fav_x", {"solvent_1": "甲苯"}, "film", "", "备注", "操作人", "A5")
        assert err is None and rec["record_id"] == "rec_y"
        assert calls["notes"] == "[A5] 备注"


# ---------------------------------------------------------------------------
# P3-4：auto-matched 文献标题解析
# ---------------------------------------------------------------------------

class TestRefTitleResolution:
    def test_auto_ref_title_resolved(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_resolve_ref_title",
                            lambda pid: {"101": "COF 成膜研究进展"}.get(str(pid)))
        html_out = gradio_app._render_refs_html(dict(_FAKE_FAV))
        assert "COF 成膜研究进展" in html_out
        assert "paper_id: 101" in html_out      # 保留 paper_id 便于溯源
        assert "手动文献" in html_out            # 手动条目不解析、不受影响

    def test_auto_ref_title_fallback(self, monkeypatch):
        """resolve 返回 None / 原名 / 模块未就位 → 回退显示 paper_id。"""
        monkeypatch.setattr(gradio_app, "_resolve_ref_title", lambda pid: None)
        html_out = gradio_app._render_refs_html(dict(_FAKE_FAV))
        assert "<b>101</b>" in html_out

    def test_resolve_ref_title_module_missing(self):
        # references.titles 未就位时应静默返回 None（就位则任意合法结果）
        out = gradio_app._resolve_ref_title("40")
        assert out is None or isinstance(out, str)


# ---------------------------------------------------------------------------
# P3-1：页⑤ 方案迭代（RAG 建议回显 + 记录摘要）
# ---------------------------------------------------------------------------

_FAKE_SUG = {
    "suggestion_id": "sug_20260722_001",
    "favorite_id": "fav_20260722_001",
    "type": "condition_adjust",
    "payload": {"adjustments": [
        {"field": "modulator", "from": "苯胺 13.7 μL", "to": "苯胺 10 μL",
         "rationale": "降低调制剂用量"},
    ]},
    "evidence_refs": [
        {"kind": "literature", "ref": "10.1000/xyz", "note": "类似体系"},
        {"kind": "experiment_record", "ref": "rec_20260722_001", "note": ""},
    ],
    "created_at": "2026-07-22T10:00:00+08:00",
    "status": "new",
}


class TestIterationTab:
    def test_render_suggestion_card(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_fav_get",
                            lambda fid: (dict(_FAKE_FAV), None))
        monkeypatch.setattr(gradio_app, "_resolve_ref_title", lambda ref: None)
        html_out = gradio_app._render_suggestion_cards([dict(_FAKE_SUG)])
        assert "条件调整" in html_out and "新建议" in html_out
        assert "苯胺 13.7 μL" in html_out and "苯胺 10 μL" in html_out
        assert "10.1000/xyz" in html_out and "rec_20260722_001" in html_out
        assert "A2 × TAPT" in html_out              # 关联收藏名
        assert "sug_20260722_001" in html_out and "2026-07-22" in html_out

    def test_render_new_candidate_card(self, monkeypatch):
        sug = dict(_FAKE_SUG, type="new_candidate",
                   payload={"aldehyde": {"smiles": "O=CC=C", "name": "A9"},
                            "amine": {"smiles": "NC=C", "name": "B9"},
                            "rationale": "池内高分组合"},
                   favorite_id=None)
        html_out = gradio_app._render_suggestion_cards([sug])
        assert "新候选单体对" in html_out and "A9 × B9" in html_out
        assert "池内高分组合" in html_out

    def test_module_missing_placeholder(self, monkeypatch):
        monkeypatch.setattr(
            gradio_app, "_sug_list",
            lambda favorite_id=None: (None, "⏳ RAG 建议模块尚未上线"))
        monkeypatch.setattr(gradio_app, "_rec_list",
                            lambda favorite_id=None: ([], None))
        monkeypatch.setattr(gradio_app, "_fav_list", lambda: ([], None))
        summary, timeline, sug_html, sel, gen_sel, status = \
            gradio_app.refresh_iteration_tab("")
        assert "尚未上线" in sug_html and "尚未上线" in status
        assert "暂无实验记录" in summary

    def test_refresh_with_data_and_filter(self, monkeypatch):
        seen = {}

        def _sug(favorite_id=None):
            seen["fid"] = favorite_id
            return [dict(_FAKE_SUG)], None

        monkeypatch.setattr(gradio_app, "_sug_list", _sug)
        monkeypatch.setattr(gradio_app, "_rec_list",
                            lambda favorite_id=None: ([dict(_FAKE_REC)], None))
        monkeypatch.setattr(gradio_app, "_fav_list",
                            lambda: ([dict(_FAKE_FAV)], None))
        monkeypatch.setattr(gradio_app, "_fav_get",
                            lambda fid: (dict(_FAKE_FAV), None))
        monkeypatch.setattr(gradio_app, "_resolve_ref_title", lambda ref: None)
        summary, timeline, sug_html, sel, gen_sel, status = \
            gradio_app.refresh_iteration_tab("fav_20260722_001")
        assert seen["fid"] == "fav_20260722_001"     # 过滤透传
        assert "共 **1** 条实验记录" in summary and "⛔ 失败 1" in summary
        assert "共 1 条建议" in status and "条件调整" in sug_html
        assert "预测 0.650" in timeline               # 复用页④ 时间线
        choices = [tuple(c) for c in sel["choices"]]
        assert ("全部", "") in choices
        assert any(c[1] == "fav_20260722_001" for c in choices)

    def test_records_summary_counts(self):
        recs = [dict(_FAKE_REC, outcome="film"), dict(_FAKE_REC, outcome="failed")]
        summary = gradio_app._records_summary(recs)
        assert "共 **2** 条" in summary and "✓ 成膜 1" in summary
        assert "⛔ 失败 1" in summary and "2026-07-22" in summary


# ---------------------------------------------------------------------------
# 任务C：页⑤ 自然语言方案迭代（subprocess 调 minimax orchestrator）
# ---------------------------------------------------------------------------

class TestIterateSuggest:
    """monkeypatch subprocess.run，验证成功 / 失败 / 超时的 UI 行为。"""

    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch):
        # 保证解释器/脚本路径存在检查通过；刷新建议区不依赖真实 suggestions 模块
        monkeypatch.setattr(gradio_app, "ITERATE_PYTHON", sys.executable)
        monkeypatch.setattr(gradio_app, "ITERATE_SCRIPT",
                            Path(gradio_app.__file__))
        monkeypatch.setattr(gradio_app, "refresh_suggestions",
                            lambda fav_filter="": ("<html/>", "共 0 条建议。"))

    def test_success_writes_and_refresh(self, monkeypatch):
        """exit 0 + stdout JSON 摘要 → 状态列出写入的 suggestion_id 并刷新。"""
        called = {}

        def _fake_run(cmd, **kwargs):
            called["cmd"] = cmd
            called["kwargs"] = kwargs
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout='日志行...\n{"written": ["sug_20260723_001", '
                       '"sug_20260723_002"], "count": 2}\n',
                stderr="")

        monkeypatch.setattr(gradio_app.subprocess, "run", _fake_run)
        html_out, status = gradio_app.run_iterate_suggest(
            "上次失败了怎么调", "fav_20260722_001")
        assert html_out == "<html/>"                      # 成功 → 刷新建议区
        assert "sug_20260723_001" in status and "sug_20260723_002" in status
        assert "✓" in status and "2 条" in status
        # 契约：命令含脚本与 --favorite-id / --question
        assert called["cmd"][1] == str(gradio_app.ITERATE_SCRIPT)
        assert "--favorite-id" in called["cmd"] and "--question" in called["cmd"]
        assert called["kwargs"]["timeout"] == 180
        assert called["kwargs"]["capture_output"] is True
        assert called["kwargs"]["errors"] == "replace"

    def test_failure_exit_nonzero(self, monkeypatch):
        """exit 非 0 → 建议区不刷新，状态回显 stderr 末行人读错误。"""
        def _fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                args=cmd, returncode=2, stdout="",
                stderr="Traceback...\nValueError: 没有任何实验记录可检索")

        monkeypatch.setattr(gradio_app.subprocess, "run", _fake_run)
        html_out, status = gradio_app.run_iterate_suggest("为什么失败", "fav_x")
        assert "生成失败" in status and "exit 2" in status
        assert "没有任何实验记录可检索" in status        # stderr 人读错误回显

    def test_timeout_message(self, monkeypatch):
        """subprocess.TimeoutExpired → 明确超时提示，不抛异常。"""
        def _fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=180)

        monkeypatch.setattr(gradio_app.subprocess, "run", _fake_run)
        html_out, status = gradio_app.run_iterate_suggest("怎么调", "")
        assert "超时" in status and "180" in status

    def test_empty_question_no_subprocess(self, monkeypatch):
        """空问题直接拦截，不启动子进程。"""
        monkeypatch.setattr(
            gradio_app.subprocess, "run",
            lambda *a, **k: pytest.fail("空问题不应调 subprocess.run"))
        _, status = gradio_app.run_iterate_suggest("   ", "fav_x")
        assert "请先输入问题" in status

    def test_python_missing_message(self, monkeypatch):
        """E:\\python3.12\\python.exe 不存在 → 明确提示文案。"""
        monkeypatch.setattr(gradio_app, "ITERATE_PYTHON",
                            r"E:\nonexistent_venv_xyz\python.exe")
        _, status = gradio_app.run_iterate_suggest("怎么调", "")
        assert "找不到 orchestrator 解释器" in status


# ---------------------------------------------------------------------------
# P4a：页④ 收藏联动过滤 / 表单重置
# ---------------------------------------------------------------------------

class TestRecordsTabP4a:
    def test_timeline_filtered_by_favorite(self, monkeypatch):
        """修复 a：选中收藏且未开「显示全部」→ list_records(favorite_id=...)。"""
        seen = {}

        def _rec_list(favorite_id=None):
            seen["fid"] = favorite_id
            return [dict(_FAKE_REC)], None

        monkeypatch.setattr(gradio_app, "_rec_list", _rec_list)
        monkeypatch.setattr(gradio_app, "_fav_list", lambda: ([], None))
        _, html_out, _pick = gradio_app.refresh_records_tab("fav_20260722_001", False)
        assert seen["fid"] == "fav_20260722_001"
        assert "rec_20260722_001" in html_out

    def test_show_all_bypasses_filter(self, monkeypatch):
        seen = {}

        def _rec_list(favorite_id=None):
            seen["fid"] = favorite_id
            return [], None

        monkeypatch.setattr(gradio_app, "_rec_list", _rec_list)
        monkeypatch.setattr(gradio_app, "_fav_list", lambda: ([], None))
        gradio_app.refresh_records_tab("fav_20260722_001", True)
        assert seen["fid"] is None           # 显示全部 → 不过滤
        gradio_app.refresh_records_tab("", False)
        assert seen["fid"] is None           # 未选收藏 → 不过滤

    def test_fav_change_resets_form_and_filters(self, monkeypatch):
        """修复 a+c：收藏下拉 change → 重置全部表单字段 + 时间线过滤。"""
        seen = {}
        monkeypatch.setattr(
            gradio_app, "_rec_list",
            lambda favorite_id=None: seen.update(fid=favorite_id)
            or ([dict(_FAKE_REC)], None))
        out = gradio_app.on_record_fav_change("fav_20260722_001", False)
        timeline, resets = out[0], out[2:]  # out[1] 为记录管理下拉
        assert seen["fid"] == "fav_20260722_001"
        assert "rec_20260722_001" in timeline
        assert len(resets) == 17
        # 文本字段重置为空值更新；结果 radio 回到「成膜」；游离勾选关闭
        assert resets[0]["value"] == ""      # 实验编号清空
        assert resets[9]["value"] == "成膜"
        assert resets[13]["value"] is False  # 游离勾选

    def test_show_all_toggle_refreshes_only_timeline(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_rec_list",
                            lambda favorite_id=None: ([dict(_FAKE_REC)], None))
        html_out, _pick = gradio_app.on_show_all_toggle("fav_x", True)
        assert "rec_20260722_001" in html_out

    def test_timeline_shows_experiment_no(self):
        rec = dict(_FAKE_REC, experiment_no="A5")
        timeline = gradio_app._render_records_timeline([rec])
        assert "实验编号：A5" in timeline

    def test_new_condition_labels(self):
        rec = dict(_FAKE_REC, conditions={"solvent_1": "甲苯",
                                          "solvent_2": "二氧六环",
                                          "eluent": "氯仿"})
        timeline = gradio_app._render_records_timeline([rec])
        assert "溶剂一：甲苯" in timeline and "溶剂二：二氧六环" in timeline
        assert "洗脱剂：氯仿" in timeline


# ---------------------------------------------------------------------------
# P4b：设置页 / 单体性质卡 / 方案卡模板
# ---------------------------------------------------------------------------

class _FakeLLMClient:
    """src.llm.client 桩：get_settings/save_settings/test_connection 签名钉死。"""

    saved = None

    @staticmethod
    def get_settings():
        return {"configured": True, "base_url": "https://api.example.com/v1",
                "model": "test-model", "api_key_masked": "sk-***wxyz",
                "source": "local"}

    @staticmethod
    def save_settings(base_url, api_key, model):
        _FakeLLMClient.saved = (base_url, api_key, model)

    @staticmethod
    def test_connection():
        return True, "模型 test-model 可用"


class _FakeLLMClientUnconfigured:
    @staticmethod
    def get_settings():
        return {"configured": False, "base_url": "", "model": "",
                "api_key_masked": "", "source": ""}

    @staticmethod
    def save_settings(base_url, api_key, model):
        pass

    @staticmethod
    def test_connection():
        return False, "未配置"


class TestSettingsTab:
    def test_load_masks_key(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_load_llm_client",
                            lambda: (_FakeLLMClient, None))
        base_url, key, model, status = gradio_app.settings_load()
        assert base_url == "https://api.example.com/v1"
        assert key == "sk-***wxyz"           # 掩码显示，绝不回显原文
        assert model == "test-model" and "✓" in status

    def test_load_unconfigured(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_load_llm_client",
                            lambda: (_FakeLLMClientUnconfigured, None))
        _, _, _, status = gradio_app.settings_load()
        assert "未配置" in status

    def test_save_calls_pinned_signature(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_load_llm_client",
                            lambda: (_FakeLLMClient, None))
        _FakeLLMClient.saved = None
        st = gradio_app.settings_save("https://api.example.com/v1",
                                      "sk-newkey123456", "m1")
        assert "✓" in st
        assert _FakeLLMClient.saved == ("https://api.example.com/v1",
                                        "sk-newkey123456", "m1")
        assert "sk-newkey123456" not in st   # 状态文案不回显 key

    def test_save_rejects_masked_key(self, monkeypatch):
        """掩码值不能直接保存——防止把掩码当真 key 写盘。"""
        monkeypatch.setattr(gradio_app, "_load_llm_client",
                            lambda: (_FakeLLMClient, None))
        _FakeLLMClient.saved = None
        st = gradio_app.settings_save("https://api.example.com/v1",
                                      "sk-***wxyz", "m1")
        assert "⚠️" in st and "重新输入" in st
        assert _FakeLLMClient.saved is None

    def test_test_connection(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_load_llm_client",
                            lambda: (_FakeLLMClient, None))
        assert "✓ 连接成功" in gradio_app.settings_test_connection()

    def test_module_missing_friendly(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_load_llm_client",
                            lambda: (None, "⏳ LLM 客户端模块尚未上线（后端开发中）。"))
        assert "尚未上线" in gradio_app.settings_load()[3]
        assert "尚未上线" in gradio_app.settings_save("u", "k", "m")
        assert "尚未上线" in gradio_app.settings_test_connection()


_FAKE_PROPS = {
    "facts": {"mw": 106.12, "xlogp": 1.5, "tpsa": 17.07, "hbd": 0, "hba": 1,
              "aromatic_rings": 1, "f_count": 0, "rotatable_bonds": 1},
    "narrative": "苯甲醛类单体，预计芳香溶剂中溶解性较好。",
    "narrative_source": "llm",
}


class TestMonomerPropCards:
    def test_render_facts_and_llm(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_monomer_properties",
                            lambda s, n="": (dict(_FAKE_PROPS), None))
        html_out = gradio_app.monomer_prop_card("O=Cc1ccccc1", "苯甲醛")
        assert "单体性质卡" in html_out and "苯甲醛" in html_out
        assert "106.12" in html_out and "XlogP" in html_out
        assert "芳环数" in html_out and "可旋转键" in html_out
        assert "LLM 生成，供参考" in html_out
        assert "溶解性较好" in html_out

    def test_llm_none_degrades(self, monkeypatch):
        props = dict(_FAKE_PROPS, narrative=None, narrative_source="none")
        monkeypatch.setattr(gradio_app, "_monomer_properties",
                            lambda s, n="": (props, None))
        html_out = gradio_app.monomer_prop_card("O=Cc1ccccc1", "苯甲醛")
        assert "LLM 解读不可用" in html_out and "RDKit" in html_out

    def test_module_missing(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_monomer_properties",
                            lambda s, n="": (None, "⏳ 单体性质卡模块尚未上线（后端开发中）。"))
        html_out = gradio_app.monomer_prop_card("O=Cc1ccccc1", "苯甲醛")
        assert "尚未上线" in html_out

    def test_empty_smiles_returns_empty(self):
        assert gradio_app.monomer_prop_card("  ", "x") == ""

    def test_pair_refresh(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_monomer_properties",
                            lambda s, n="": (dict(_FAKE_PROPS), None))
        ald_html, amine_html = gradio_app.monomer_prop_cards_for_pair(
            "O=Cc1ccccc1", "Nc1ccc(N)cc1")
        assert "单体性质卡" in ald_html and "单体性质卡" in amine_html


class _FakePlanTemplates:
    saved = None

    @staticmethod
    def list_templates():
        return [{"id": "builtin_hou_v3_9", "name": "侯老师界面法 v3.9",
                 "builtin": True},
                {"id": "tpl_custom1", "name": "自定义·文献A法", "builtin": False}]

    @staticmethod
    def get_template(tid):
        if tid == "tpl_custom1":
            return {"id": "tpl_custom1", "name": "自定义·文献A法"}
        raise KeyError(tid)

    @staticmethod
    def extract_template_from_docx(path):
        return {"id": "tpl_new", "name": "提取模板",
                "source": "user-docx", "conditions": {"solvent": "甲苯"},
                "steps": ["第一步"], "checklist": [{"item": "核对"}],
                "hints_rules": [{"hint": "提示"}]}

    @staticmethod
    def save_template(tpl):
        _FakePlanTemplates.saved = tpl
        return tpl


class TestPlanTemplates:
    def test_choices_include_user_templates(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_load_plan_templates",
                            lambda: (_FakePlanTemplates, None))
        upd = gradio_app.template_choices_update()
        choices = [tuple(c) for c in upd["choices"]]
        assert (gradio_app._DEFAULT_TEMPLATE_LABEL, "") in choices
        assert ("自定义·文献A法", "tpl_custom1") in choices
        assert upd["value"] == ""

    def test_module_missing_default_only(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_load_plan_templates",
                            lambda: (None, "⏳ 方案卡模板模块尚未上线（后端开发中）。"))
        upd = gradio_app.template_choices_update()
        assert upd["choices"] == [(gradio_app._DEFAULT_TEMPLATE_LABEL, "")]

    def test_resolve_template_choice(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_load_plan_templates",
                            lambda: (_FakePlanTemplates, None))
        assert gradio_app.resolve_template_choice("") is None
        tpl = gradio_app.resolve_template_choice("tpl_custom1")
        assert tpl["name"] == "自定义·文献A法"
        assert gradio_app.resolve_template_choice("不存在") is None

    def test_upload_preview(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_load_plan_templates",
                            lambda: (_FakePlanTemplates, None))
        monkeypatch.setattr(gradio_app, "_llm_configured", lambda: True)
        tpl, md, st = gradio_app.template_upload_preview("fake.docx")
        assert tpl["id"] == "tpl_new" and "提取模板" in md
        assert "甲苯" in md and "✓" in st

    def test_upload_requires_llm(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_load_plan_templates",
                            lambda: (_FakePlanTemplates, None))
        monkeypatch.setattr(gradio_app, "_llm_configured", lambda: False)
        tpl, md, st = gradio_app.template_upload_preview("fake.docx")
        assert tpl is None and "未配置 LLM" in st and "设置页" in st

    def test_confirm_save(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_load_plan_templates",
                            lambda: (_FakePlanTemplates, None))
        _FakePlanTemplates.saved = None
        pending = {"id": "tpl_new", "name": "提取模板", "source": "user-docx",
                   "conditions": {}, "steps": [], "checklist": [],
                   "hints_rules": []}
        st, upd = gradio_app.template_confirm_save(pending, "改名模板")
        assert "✓" in st and "改名模板" in st
        assert _FakePlanTemplates.saved["name"] == "改名模板"
        st, _ = gradio_app.template_confirm_save(None, "")
        assert "⚠️" in st


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
