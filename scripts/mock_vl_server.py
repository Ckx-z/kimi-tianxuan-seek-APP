"""端到端验证用的「假视觉模型」服务（OpenAI 兼容 /chat/completions）。

为什么需要它：真实云端 VL 需要用户的 Key，而整条链路（设置 → resolve_config →
analyze_image → caption_figure → 写库 caption_source=vision → 助手工具）
在任何一处出错都会表现为「图没标注」，无法定位。这里用一个本地假端点把链路跑通，
把「我们的代码对不对」和「云端模型答得好不好」彻底分开。

行为：
- 收到含 image_url 的请求 → 返回固定结构的图分析 JSON（与 _PROMPT 约定一致）；
- 记录每次请求的图片字节数到 stdout，便于确认图确实被传上去了；
- 可以用 --fail 模拟端点报错（验证「失败不静默、不写库」）；
- 可以用 --no-json 模拟模型返回非 JSON（验证解析兜底）。

用法：
    E:\\ANACONDA\\python.exe scripts\\mock_vl_server.py --port 18899
    E:\\ANACONDA\\python.exe scripts\\mock_vl_server.py --port 18899 --fail
"""

from __future__ import annotations

import argparse
import base64
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ANALYSIS = {
    "figure_type": "spectra",
    "technique": "PXRD",
    "description": "假模型：该图为 PXRD 谱，2θ 5-30° 区间出现三个尖锐衍射峰。",
    "metrics": [
        {"name": "2θ峰位", "value": 4.72, "unit": "°"},
        {"name": "2θ峰位", "value": 7.31, "unit": "°"},
        {"name": "比表面积", "value": 1820.0, "unit": "m2/g"},
    ],
    "confidence": "high",
    "notes": "假模型固定输出，仅用于链路验证。",
}


class Handler(BaseHTTPRequestHandler):
    fail = False
    no_json = False

    def log_message(self, fmt, *args):        # 静音默认日志，保留我们自己的
        pass

    def do_POST(self):                        # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        if not self.path.endswith("/chat/completions"):
            self._json(404, {"error": {"message": f"未知路径 {self.path}"}})
            return
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            self._json(400, {"error": {"message": "请求体不是 JSON"}})
            return

        # 统计图片大小，确认图确实被 base64 送进来了
        img_bytes = 0
        for msg in body.get("messages") or []:
            content = msg.get("content")
            if isinstance(content, list):
                for part in content:
                    if part.get("type") == "image_url":
                        url = str((part.get("image_url") or {}).get("url") or "")
                        m = re.match(r"data:[^;]+;base64,(.*)$", url, re.S)
                        if m:
                            try:
                                img_bytes += len(base64.b64decode(m.group(1)))
                            except Exception:
                                pass
        print(f"[mock-vl] model={body.get('model')} max_tokens={body.get('max_tokens')} "
              f"图片字节={img_bytes}", flush=True)

        if self.fail:
            self._json(500, {"error": {"message": "假模型：故意失败（--fail）"}})
            return

        if self.no_json:
            content = "这是一段普通文字，不是 JSON。"
        else:
            content = json.dumps(ANALYSIS, ensure_ascii=False)

        self._json(200, {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "model": body.get("model") or "mock-vl",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        })

    def _json(self, code: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18899)
    ap.add_argument("--fail", action="store_true", help="所有请求返回 500")
    ap.add_argument("--no-json", action="store_true", help="返回非 JSON 文本")
    args = ap.parse_args()
    Handler.fail = args.fail
    Handler.no_json = args.no_json
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[mock-vl] listening http://127.0.0.1:{args.port}/v1  "
          f"fail={args.fail} no_json={args.no_json}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
