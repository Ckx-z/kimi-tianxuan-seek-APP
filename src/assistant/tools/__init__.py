"""科研助手 MVP 工具集：每个工具一个模块，包一层现有真实能力。

统一契约（继承 openhanako toolOk/toolError）：handler 返回
{"text": LLM 可读中文摘要, "details": 结构化数据, "is_error": bool}；
handler 内不吞异常静默，而是转 is_error=True 让 LLM 知道失败了。
"""

from __future__ import annotations

from . import graphrag, predict, records

__all__ = ["predict", "graphrag", "records"]
