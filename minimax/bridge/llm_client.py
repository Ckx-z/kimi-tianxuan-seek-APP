"""
bridge/llm_client.py
====================
统一 LLM 端点配置与 OpenAI 兼容 chat 调用（主 MiniMax，备 longcat 回落）。

密钥/端点读取顺序（高 -> 低）:
1. 进程环境变量（MINIMAX_API_KEY / LONGCAT_API_KEY 等，可用 setx 设用户级）
2. minimax/config/secrets.local.json（gitignored，绝不提交）

切换方式:
- 默认 chat_completion() 按 fallback_order 依次尝试：主端点未配置 key 或请求失败
  时自动回落 longcat。
- 显式 chat_completion(..., provider='longcat') 可强制走备用端点。
- 注意: embedding（embo-01, 1536 维）仅 MiniMax 提供，tianxuan/core 索引均以此
  构建，不做 fallback；get_minimax_api_key() 供 embedding 路径取 key。
"""
import os
import json
from pathlib import Path

SECRETS_PATH = Path(__file__).resolve().parent.parent / 'config' / 'secrets.local.json'

# 环境变量名 -> secrets.local.json 中的 (provider, 字段)
_DEFAULTS = {
    'minimax': {
        'base_url': 'https://api.minimax.chat/v1',
        'chat_model': 'MiniMax-Text-01',
        'embed_model': 'embo-01',
        'api_key_env': 'MINIMAX_API_KEY',
    },
    'longcat': {
        'base_url': 'https://api.longcat.chat/openai/v1',
        'chat_model': 'LongCat-2.0',
        'api_key_env': 'LONGCAT_API_KEY',
    },
}
FALLBACK_ORDER = ['minimax', 'longcat']

_secrets_cache = None


def _load_secrets():
    """读 gitignored 的 config/secrets.local.json（不存在则返回空 dict）"""
    global _secrets_cache
    if _secrets_cache is None:
        if SECRETS_PATH.exists():
            with open(SECRETS_PATH, encoding='utf-8') as f:
                _secrets_cache = json.load(f)
        else:
            _secrets_cache = {}
    return _secrets_cache


def get_provider_config(provider):
    """合并默认、secrets.local.json 与环境变量，返回 provider 配置 dict。

    返回字段: provider, base_url, chat_model, api_key (可能为 None)
    """
    secrets = _load_secrets().get('providers', {}).get(provider, {})
    cfg = dict(_DEFAULTS.get(provider, {}))
    cfg.update({k: v for k, v in secrets.items() if k != 'api_key' and not k.startswith('_')})
    env_name = cfg.get('api_key_env', f'{provider.upper()}_API_KEY')
    # 环境变量优先于文件
    cfg['api_key'] = os.environ.get(env_name) or secrets.get('api_key')
    cfg['provider'] = provider
    return cfg


def get_minimax_api_key():
    """embedding / 旧代码路径取 MiniMax key（env 优先，其次 secrets.local.json）"""
    key = os.environ.get('MINIMAX_API_KEY') or os.environ.get('MINIMAX_KEY')
    if key:
        return key
    return get_provider_config('minimax').get('api_key')


def get_fallback_order():
    """主备顺序，secrets.local.json 的 fallback_order 可覆盖默认"""
    order = _load_secrets().get('fallback_order')
    return list(order) if order else list(FALLBACK_ORDER)


def chat_completion(messages, model=None, provider=None, max_tokens=2000,
                    temperature=0.3, timeout=60):
    """OpenAI 兼容 chat completion，主端点失败/未配置时按 fallback_order 回落。

    provider: 指定则只用该端点；None 则按 fallback_order 依次尝试。
    返回: (content: str, used_provider: str)
    失败: 抛出最后一次异常（所有端点均失败时）。
    """
    import requests

    order = [provider] if provider else get_fallback_order()
    last_err = None
    for name in order:
        cfg = get_provider_config(name)
        if not cfg.get('api_key'):
            last_err = RuntimeError(f'{name} API key 未配置（env 或 secrets.local.json）')
            continue
        try:
            r = requests.post(
                cfg['base_url'].rstrip('/') + '/chat/completions',
                headers={'Authorization': f"Bearer {cfg['api_key']}",
                         'Content-Type': 'application/json'},
                json={
                    'model': model or cfg['chat_model'],
                    'messages': messages,
                    'max_tokens': max_tokens,
                    'temperature': temperature,
                },
                timeout=timeout,
            )
            r.raise_for_status()
            j = r.json()
            content = j['choices'][0]['message'].get('content') or ''
            return content, name
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f'所有 LLM 端点均失败: {last_err}')


if __name__ == '__main__':
    # 连通性自检（不回显 key）
    for name in get_fallback_order():
        cfg = get_provider_config(name)
        key = cfg.get('api_key') or ''
        masked = (key[:6] + '...' + key[-4:]) if len(key) > 12 else '(未配置)'
        print(f"[{name}] base_url={cfg.get('base_url')} model={cfg.get('chat_model')} key={masked}")
