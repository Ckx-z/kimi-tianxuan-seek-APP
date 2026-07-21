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
                            lambda a, b, an, bn: (dict(_FAKE_CARD), None))
        html_out, status = gradio_app.plan_card_for_input("O=Cc1ccccc1", "Nc1ccc(N)cc1")
        assert "✓" in status
        assert "侯老师法" in html_out and "防错清单" in html_out
        assert "6M≠18M" in html_out  # {item, detail} 拼接
        assert "溶剂" in html_out and "催化剂" in html_out  # 英文键→中文标签
        assert "含氟单体注意溶解性" in html_out

    def test_backend_missing(self, monkeypatch):
        monkeypatch.setattr(gradio_app, "_plan_generate",
                            lambda a, b, an, bn: (None, "⏳ 方案卡模块尚未上线（后端开发中）。"))
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
            lambda fid, cond, out, s, n, op: created.update(
                fid=fid, outcome=out) or (dict(_FAKE_REC), None))
        monkeypatch.setattr(gradio_app, "_rec_list",
                            lambda favorite_id=None: ([dict(_FAKE_REC)], None))
        st, timeline, _ = gradio_app.submit_record(
            "fav_20260722_001", "甲苯", "", "6M 乙酸", "120", "3",
            "先醛后胺", "失败", "膜脆", "测试员", "乙酸用错")
        assert "✓" in st and "rec_20260722_001" in st
        assert created["outcome"] == "failed"  # 中文→契约枚举
        assert "预测 0.650 ± 0.040" in timeline
        assert "实际" in timeline and "⛔ 失败" in timeline
        assert "溶剂：甲苯" in timeline

    def test_submit_requires_favorite(self):
        st, _, _ = gradio_app.submit_record(
            "", "甲苯", "", "", "", "", "", "成膜", "", "", "")
        assert "收藏" in st and "⚠️" in st

    def test_submit_requires_content(self):
        st, _, _ = gradio_app.submit_record(
            "fav_x", "", "", "", "", "", "", "成膜", "", "", "")
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
        _, recs_html = gradio_app.refresh_records_tab()
        assert "尚未上线" in recs_html


# ---------------------------------------------------------------------------
# Bug 1 回归：主分数口径 —— 主展示分数 == 路由树模型分，任何位置不取 max
# ---------------------------------------------------------------------------

class TestMainScoreContract:
    """树 0.30 / GNN 0.90 / 综合 0.60 三分离：主分数必须是 0.30（树），
    取 max 会得 0.90，取综合会得 0.60——均可被断言区分。"""

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

    def test_headline_is_tree_not_max(self, pred_stub):
        prob_text = gradio_app.predict("Aald", "Bami")[0]
        headline = prob_text.split("### 成膜打分")[0]
        assert "0.300" in headline      # 主分数 = 路由树模型分
        assert "0.900" not in headline  # 不取 max(GNN,…)
        assert "0.600" not in headline  # 不用综合分冒充
        # GNN / 综合仅作对照，且综合明确标注
        assert "**GNN v5.3**: 0.900" in prob_text
        assert "综合打分（树与 GNN 平均，仅对照参考）" in prob_text

    def test_batch_score_is_tree_not_max(self, pred_stub):
        state, table, _ = gradio_app.batch_predict([], [], "Aald,Bami", None)
        assert state["rows"][0]["score"] == 0.30
        assert table[0][2] == 0.3

    def test_snapshot_payload_is_tree_not_max(self):
        payload = gradio_app._snapshot_payload(self._pred("A", "B"))
        assert payload["score"] == 0.30

    def test_log_record_is_tree_not_max(self, monkeypatch):
        rec = gradio_app._build_log_record("A", "B", self._pred("A", "B"), "single")
        assert rec["score"] == 0.30

    def test_tree_missing_no_substitute(self, monkeypatch):
        """树模型未出分时：主分数区给提示，绝不拿 GNN/综合顶替。"""
        pred = self._pred("A", "B")
        del pred["tree_probability"]

        class _P2:
            tree_available = False

            def predict(self, a, b):
                return dict(pred)

        monkeypatch.setattr(gradio_app, "_get_predictor", lambda: _P2())
        monkeypatch.setattr(gradio_app, "_log_prediction", lambda rec: True)
        monkeypatch.setattr(gradio_app, "_explain_tree_score", lambda p, a, b: "")
        monkeypatch.setattr(gradio_app, "_structure_images",
                            lambda a, b: (None, None, None, ""))
        monkeypatch.setattr(gradio_app, "_find_similar_cases",
                            lambda a, b, top_k=3: ([], None))
        prob_text = gradio_app.predict("Aald", "Bami")[0]
        headline = prob_text.split("### 成膜打分")[0]
        assert "树模型未出分" in headline
        assert "score-big" not in headline  # 大字号区不显示任何替代分
        state, _, _ = gradio_app.batch_predict([], [], "Aald,Bami", None)
        assert state["rows"][0]["score"] is None
        assert "树模型未出分" in state["rows"][0]["reason"]


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
            lambda a, b, cond, out, s, n, op: created.update(
                ald=a, amine=b, fid_checked=True) or (dict(_FAKE_REC), None))
        monkeypatch.setattr(gradio_app, "_rec_list",
                            lambda favorite_id=None: ([dict(_FAKE_REC)], None))
        st, timeline, _ = gradio_app.submit_record(
            "", "甲苯", "", "6M 乙酸", "120", "3", "", "成膜", "", "测试员", "",
            True, "O=Cc1ccccc1", "Nc1ccc(N)cc1")
        assert "✓" in st
        assert created["ald"] == "O=Cc1ccccc1"
        assert created["amine"] == "Nc1ccc(N)cc1"

    def test_free_record_requires_smiles(self):
        st, _, _ = gradio_app.submit_record(
            "", "甲苯", "", "", "", "", "", "成膜", "", "", "",
            True, "", "Nc1ccc(N)cc1")
        assert "⚠️" in st and "SMILES" in st

    def test_linked_mode_unchanged(self, monkeypatch):
        created = {}
        monkeypatch.setattr(
            gradio_app, "_rec_create_linked",
            lambda fid, cond, out, s, n, op: created.update(fid=fid)
            or (dict(_FAKE_REC), None))
        monkeypatch.setattr(gradio_app, "_rec_list",
                            lambda favorite_id=None: ([], None))
        st, _, _ = gradio_app.submit_record(
            "fav_20260722_001", "甲苯", "", "", "", "", "", "成膜", "", "", "",
            False, "", "")
        assert "✓" in st and created["fid"] == "fav_20260722_001"

    def test_free_wrapper_calls_pinned_signature(self, monkeypatch):
        """游离记录封装按钉死签名调 create_record（关键字传参）。"""
        calls = {}

        class _Store:
            @staticmethod
            def create_record(**kw):
                calls.update(kw)
                return {"record_id": "rec_x"}

        monkeypatch.setattr(gradio_app, "_load_records_store",
                            lambda: (_Store, None))
        rec, err = gradio_app._rec_create_free(
            "ALD", "AMN", {"solvent": "甲苯"}, "film", "", "备注", "操作人")
        assert err is None and rec["record_id"] == "rec_x"
        assert calls["favorite_id"] is None
        assert calls["aldehyde_smiles"] == "ALD"
        assert calls["amine_smiles"] == "AMN"
        assert calls["conditions"] == {"solvent": "甲苯"}
        assert calls["outcome"] == "film"
        assert calls["notes"] == "备注" and calls["operator"] == "操作人"


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
        summary, timeline, sug_html, sel, status = gradio_app.refresh_iteration_tab("")
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
        summary, timeline, sug_html, sel, status = \
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
