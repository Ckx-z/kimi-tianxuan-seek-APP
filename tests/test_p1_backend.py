"""P1 后端支撑模块测试：CAS 解析 / 相似案例 / 预测日志 / 内置单体库。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from recommend.similar_cases import find_similar_film_cases  # noqa: E402
from utils import cas_lookup  # noqa: E402
from utils import predict_log  # noqa: E402

ALD = "O=CC1=C(C=O)C(=O)C(C=O)=C1O"  # Tp
AMINE = "Nc1ccc(N)cc1"  # Pa


class TestBuiltinMonomers:
    def test_library_shape(self):
        items = json.loads(
            (PROJECT_ROOT / "data" / "builtin_monomers.json").read_text(
                encoding="utf-8"
            )
        )
        assert len(items) == 17  # 4 节点 + A1-A7 + B1-B6，不含 H 组
        for m in items:
            assert set(m) == {"name", "role", "cas", "smiles", "short_desc"}
            assert m["role"] in ("aldehyde", "amine")
            assert cas_lookup._valid_smiles(m["smiles"])

    def test_node_role_assignment(self):
        items = {
            m["name"]: m
            for m in json.loads(
                (PROJECT_ROOT / "data" / "builtin_monomers.json").read_text(
                    encoding="utf-8"
                )
            )
        }
        assert items["TAPT"]["role"] == "amine"  # 三胺节点
        assert items["TAPB"]["role"] == "amine"
        assert items["TFPT"]["role"] == "aldehyde"  # 三醛节点
        assert items["TFPB"]["role"] == "aldehyde"


class TestCasLookup:
    def test_builtin_hit(self):
        r = cas_lookup.resolve_cas("14544-47-9")  # TAPT
        assert r is not None
        assert r["source"] == "builtin"
        assert r["name"] == "TAPT"
        assert cas_lookup._valid_smiles(r["smiles"])

    def test_invalid_cas_format(self):
        assert cas_lookup.resolve_cas("not-a-cas") is None
        assert cas_lookup.resolve_cas("") is None
        assert cas_lookup.resolve_cas("14544479") is None
        assert cas_lookup.resolve_cas(None) is None

    def test_cache_hit(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "cas_cache.json"
        cache_file.write_text(
            json.dumps({"50-00-0": {"smiles": "C=O", "name": "甲醛"}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(cas_lookup, "CACHE_PATH", cache_file)
        # 内置库不命中时才走缓存；50-00-0 不在内置库
        r = cas_lookup.resolve_cas("50-00-0")
        assert r == {"smiles": "C=O", "name": "甲醛", "source": "cache"}

    def test_pubchem_mock_success_writes_cache(self, tmp_path, monkeypatch):
        cache_file = tmp_path / "cas_cache.json"
        monkeypatch.setattr(cas_lookup, "CACHE_PATH", cache_file)
        monkeypatch.setattr(
            cas_lookup,
            "_fetch_pubchem",
            lambda cas: {"smiles": "C=O", "name": ""},
        )
        r = cas_lookup.resolve_cas("50-00-0")
        assert r == {"smiles": "C=O", "name": "", "source": "pubchem"}
        # 成功后写缓存
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
        assert cache["50-00-0"]["smiles"] == "C=O"

    def test_pubchem_no_network_returns_none(self, tmp_path, monkeypatch):
        # 无网络降级：_fetch_pubchem 返回 None（内部已吞掉连接异常）
        monkeypatch.setattr(cas_lookup, "CACHE_PATH", tmp_path / "nope.json")
        monkeypatch.setattr(cas_lookup, "_fetch_pubchem", lambda cas: None)
        # 同时关闭 LLM 兜底，保证本测试只验证无网络降级语义
        monkeypatch.setattr(cas_lookup, "_fetch_llm", lambda cas: None)
        assert cas_lookup.resolve_cas("50-00-0") is None

    def test_pubchem_invalid_smiles_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cas_lookup, "CACHE_PATH", tmp_path / "c.json")
        monkeypatch.setattr(
            cas_lookup,
            "_fetch_pubchem",
            lambda cas: {"smiles": "not_a_smiles_((((", "name": ""},
        )
        # 同时关闭 LLM 兜底，保证本测试只验证 PubChem 非法 SMILES 拒绝语义
        monkeypatch.setattr(cas_lookup, "_fetch_llm", lambda cas: None)
        assert cas_lookup.resolve_cas("50-00-0") is None

    def test_llm_fallback_success_writes_cache(self, tmp_path, monkeypatch):
        # PubChem 失败后走 LLM 兜底：合法 SMILES 接受、source='llm' 并写缓存
        cache_file = tmp_path / "cas_cache.json"
        monkeypatch.setattr(cas_lookup, "CACHE_PATH", cache_file)
        monkeypatch.setattr(cas_lookup, "_fetch_pubchem", lambda cas: None)
        monkeypatch.setattr(
            cas_lookup,
            "_fetch_llm",
            lambda cas: {"smiles": "Nc1ccc(N)cc1", "name": ""},
        )
        r = cas_lookup.resolve_cas("106-50-3")  # 对苯二胺，不在内置库
        assert r == {"smiles": "Nc1ccc(N)cc1", "name": "", "source": "llm"}
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
        assert cache["106-50-3"]["smiles"] == "Nc1ccc(N)cc1"

    def test_llm_invalid_smiles_rejected(self, tmp_path, monkeypatch):
        # LLM hallucinate 出非法 SMILES 时 RDKit 校验拒绝，返回 None
        monkeypatch.setattr(cas_lookup, "CACHE_PATH", tmp_path / "c.json")
        monkeypatch.setattr(cas_lookup, "_fetch_pubchem", lambda cas: None)
        monkeypatch.setattr(
            cas_lookup,
            "_fetch_llm",
            lambda cas: {"smiles": "garbage_(((", "name": ""},
        )
        assert cas_lookup.resolve_cas("106-50-3") is None

    def test_llm_unconfigured_returns_none(self, monkeypatch):
        # LLM 未配置时 _fetch_llm 直接返回 None，不发请求
        monkeypatch.setattr(
            "src.llm.client.is_configured", lambda: False, raising=False
        )
        assert cas_lookup._fetch_llm("106-50-3") is None

    def test_llm_unknown_answer_rejected(self, monkeypatch):
        # LLM 自言不知（UNKNOWN）时返回 None
        class FakeClient:
            @staticmethod
            def is_configured():
                return True

            @staticmethod
            def chat_completion(messages, max_tokens, temperature, extra_body=None):
                return "UNKNOWN"

        import src.llm.client as real_client

        monkeypatch.setattr(real_client, "is_configured", FakeClient.is_configured)
        monkeypatch.setattr(real_client, "chat_completion", FakeClient.chat_completion)
        assert cas_lookup._fetch_llm("999999-99-9") is None

    def _patch_llm(self, monkeypatch, dispatch):
        """按 prompt 内容分发假回答，模拟双路查询。"""
        import src.llm.client as real_client

        def fake_chat(messages, max_tokens, temperature, extra_body=None):
            return dispatch(messages[0]["content"])

        monkeypatch.setattr(real_client, "is_configured", lambda: True)
        monkeypatch.setattr(real_client, "chat_completion", fake_chat)

    def test_llm_consistency_accept(self, monkeypatch):
        # 双路一致（CAS 直查与 名称→SMILES 结构相同）→ 接受，带名称
        def dispatch(prompt):
            if "名称" in prompt and "SMILES" not in prompt:
                return "甲醛"
            if "甲醛" in prompt:
                return "C=O"
            return "O=C"  # 直查，规范化后与 C=O 相同

        self._patch_llm(monkeypatch, dispatch)
        r = cas_lookup._fetch_llm("50-00-0")
        assert r is not None
        assert r["smiles"] == "C=O"
        assert r["name"] == "甲醛"

    def test_llm_consistency_reject(self, monkeypatch):
        # 双路不一致（直查 C=O vs 名称路径 CCO）→ 返回 None，防幻觉写缓存
        def dispatch(prompt):
            if "名称" in prompt and "SMILES" not in prompt:
                return "乙醇"
            if "乙醇" in prompt:
                return "CCO"
            return "C=O"

        self._patch_llm(monkeypatch, dispatch)
        assert cas_lookup._fetch_llm("50-00-0") is None


class TestSimilarCases:
    def test_normal_return(self):
        cases = find_similar_film_cases(ALD, AMINE, top_k=3)
        assert isinstance(cases, list)
        assert 1 <= len(cases) <= 3
        for c in cases:
            assert set(c) == {
                "aldehyde_smiles",
                "amine_smiles",
                "is_film",
                "paper_id",
                "similarity",
            }
            assert 0.0 <= c["similarity"] <= 1.0
            assert c["is_film"] >= 0.8
        # 相似度降序
        sims = [c["similarity"] for c in cases]
        assert sims == sorted(sims, reverse=True)
        # 与自身完全相同的配对应排第一（相似度 1.0）
        assert cases[0]["similarity"] == 1.0

    def test_invalid_smiles_returns_empty(self):
        assert find_similar_film_cases("junk_(((", AMINE) == []
        assert find_similar_film_cases(ALD, "") == []
        assert find_similar_film_cases(None, None) == []

    def test_missing_data_returns_empty(self, monkeypatch):
        from recommend import similar_cases

        monkeypatch.setattr(
            similar_cases, "TRAIN_CSV", PROJECT_ROOT / "data" / "nope.csv"
        )
        assert find_similar_film_cases(ALD, AMINE) == []


class TestPredictLog:
    def test_write_format(self, tmp_path, monkeypatch):
        log_file = tmp_path / "sub" / "prediction_log.jsonl"  # 目录不存在应自动建
        monkeypatch.setattr(predict_log, "LOG_PATH", log_file)
        predict_log.log_prediction({"aldehyde_smiles": ALD, "score": 0.7})
        lines = log_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["score"] == 0.7
        assert entry["schema_version"] == 1
        assert "timestamp" in entry

    def test_append_multiple(self, tmp_path, monkeypatch):
        log_file = tmp_path / "prediction_log.jsonl"
        monkeypatch.setattr(predict_log, "LOG_PATH", log_file)
        predict_log.log_prediction({"a": 1})
        predict_log.log_prediction({"b": 2})
        assert len(log_file.read_text(encoding="utf-8").strip().splitlines()) == 2

    def test_exception_silent(self, tmp_path, monkeypatch):
        # 不可序列化对象 → 静默，不抛异常
        log_file = tmp_path / "prediction_log.jsonl"
        monkeypatch.setattr(predict_log, "LOG_PATH", log_file)
        predict_log.log_prediction({"bad": object()})
        # 日志路径父级是个已存在文件 → mkdir/写入失败也应静默
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setattr(predict_log, "LOG_PATH", blocker / "x.jsonl")
        predict_log.log_prediction({"a": 1})
        predict_log.log_prediction(None)
