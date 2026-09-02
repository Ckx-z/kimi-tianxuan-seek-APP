"""COF 科研系统 REST API（未来独立前端的地基）。

只包 src/ 的纯后端能力，不依赖 Gradio；与 app/gradio_app.py 并存互不影响。
启动：uvicorn api.main:app --port 8000
"""

__version__ = "1.5.1"  # 发版时与 webapp/package.json 同步（发版纪律第 7 条）
