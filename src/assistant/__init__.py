"""「科研助手」Agent 包（MVP）。

组成：
- persona/      ming 人格（Apache-2.0 来源声明见文件头）+ 领域规则
- persona.py    system prompt 三层拼装（人格 + 领域纪律 + 注入上下文）
- registry.py   工具注册表：name → {schema(JSON Schema), handler}
- tools/        predict_film / query_graphrag / read_experiment_records
- llm_bridge.py LLM 双路径调用（function calling 优先，乱格式/不支持抛
                FunctionCallingUnsupported 由 loop 降级两段式）
- sessions.py   会话 jsonl 存储（user_data_root()/assistant/sessions/）
- context.py    页⑤转入上下文组装（单体组 + 迭代建议 + 实验记录摘要）
- loop.py       agent 主循环（max 5 轮工具调用防死循环），产出 SSE 事件 dict

红线：API key 只经 src/llm/client.py 门面，绝不打印/落盘；工具异常一律转
is_error 返回，不抛出炸流。
"""
