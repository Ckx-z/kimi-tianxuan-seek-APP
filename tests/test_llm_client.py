"""src/llm 统一客户端测试：配置链优先级、掩码、缓存命中、失败返回 None。

全部 mock HTTP（monkeypatch _http_chat），不依赖真实网络与真实密钥文件。
"""

import json

import pytest

import src.llm.client as client


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """把所有文件路径与环境变量隔离到 tmp，避免读真实配置/密钥。"""
    monkeypatch.setattr(client, "LOCAL_SETTINGS", tmp_path / "llm_settings.local.json")
    monkeypatch.setattr(client, "MINIMAX_SECRETS", tmp_path / "secrets.local.json")
    monkeypatch.setattr(client, "CACHE_DIR", tmp_path / "llm_cache")
    for var in ("COF_LLM_BASE_URL", "COF_LLM_API_KEY", "COF_LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    yield


def _write_local(data):
    client.LOCAL_SETTINGS.write_text(json.dumps(data), encoding="utf-8")


def _write_seed():
    client.MINIMAX_SECRETS.write_text(
        json.dumps(
            {
                "providers": {
                    "longcat": {
                        "base_url": "https://seed.example/v1",
                        "api_key": "seed-key-12345678",
                        "chat_model": "SeedModel",
                    }
                }
            }
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------- 配置链

def test_unconfigured():
    assert client.is_configured() is False
    s = client.get_settings()
    assert s["configured"] is False
    assert client.chat_completion([{"role": "user", "content": "hi"}]) is None
    ok, msg = client.test_connection()
    assert ok is False and "未配置" in msg


def test_priority_local_over_env_over_seed(monkeypatch):
    _write_seed()
    monkeypatch.setenv("COF_LLM_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("COF_LLM_API_KEY", "env-key-12345678")
    monkeypatch.setenv("COF_LLM_MODEL", "EnvModel")

    assert client.get_settings()["source"] == "env"

    _write_local(
        {"base_url": "https://local.example/v1", "api_key": "local-key-12345678", "model": "LocalModel"}
    )
    s = client.get_settings()
    assert s["source"] == "local_settings"
    assert s["base_url"] == "https://local.example/v1"
    assert s["model"] == "LocalModel"


def test_env_fallback_and_seed_default(monkeypatch):
    _write_seed()
    # 仅 seed 时开箱即用
    s = client.get_settings()
    assert s["source"] == "longcat_seed"
    assert s["model"] == "SeedModel"

    monkeypatch.setenv("COF_LLM_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("COF_LLM_API_KEY", "env-key-12345678")
    assert client.get_settings()["source"] == "env"


# ---------------------------------------------------------------- 掩码与保存

def test_masking_never_echoes_key():
    _write_local(
        {"base_url": "https://x.example/v1", "api_key": "sk-secret-abcdef123456", "model": "M"}
    )
    s = client.get_settings()
    assert "sk-secret-abcdef123456" not in json.dumps(s)
    assert s["api_key_masked"] == "sk-s***3456"


def test_save_settings_roundtrip():
    client.save_settings("https://a.example/v1", "sk-save-12345678", "SaveModel")
    assert client.LOCAL_SETTINGS.is_file()
    s = client.get_settings()
    assert s["configured"] and s["source"] == "local_settings"
    assert s["base_url"] == "https://a.example/v1"
    assert s["model"] == "SaveModel"
    # 明文密钥只存在于 gitignored 的 local 文件，不经 get_settings 回显
    assert "sk-save-12345678" not in json.dumps(s)


# ---------------------------------------------------------------- 调用与缓存

def _configured():
    _write_local(
        {"base_url": "https://x.example/v1", "api_key": "sk-x-12345678", "model": "M"}
    )


def test_chat_success_and_cache_hit(monkeypatch):
    _configured()
    calls = []

    def fake_http(cfg, messages, max_tokens, temperature):
        calls.append(messages)
        return "你好，COF"

    monkeypatch.setattr(client, "_http_chat", fake_http)
    msgs = [{"role": "user", "content": "介绍一下 COF"}]

    assert client.chat_completion(msgs) == "你好，COF"
    assert len(calls) == 1
    # 第二次命中缓存，不再调用
    assert client.chat_completion(msgs) == "你好，COF"
    assert len(calls) == 1
    assert len(list(client.CACHE_DIR.glob("*.json"))) == 1
    # nocache 跳过缓存
    assert client.chat_completion(msgs, nocache=True) == "你好，COF"
    assert len(calls) == 2


def test_chat_failure_returns_none(monkeypatch):
    _configured()

    def boom(cfg, messages, max_tokens, temperature):
        raise RuntimeError("network down")

    monkeypatch.setattr(client, "_http_chat", boom)
    assert client.chat_completion([{"role": "user", "content": "hi"}]) is None


def test_test_connection(monkeypatch):
    _configured()
    monkeypatch.setattr(client, "_http_chat", lambda *a, **k: "pong")
    ok, msg = client.test_connection()
    assert ok is True and "pong" in msg

    def boom(*a, **k):
        raise RuntimeError("timeout")

    monkeypatch.setattr(client, "_http_chat", boom)
    ok, msg = client.test_connection()
    assert ok is False and "连接失败" in msg
