"""网页正文抓取工具（v1.6.0 P0）：fetch_page。

正文提取用 trafilatura（未安装时降级为简单的 HTML 标签剥离，保证工具
不因依赖缺失而缺席）；安全红线：仅 http/https 公网地址，拒绝内网/环回
IP、file:// 与超长内容；超时与失败全部转 is_error 中文原因。
"""

from __future__ import annotations

import ipaddress
import re
import socket
import urllib.parse

import requests

TIMEOUT = 20      # 秒
MAX_BYTES = 2 * 1024 * 1024  # 单页上限 2MB
_MAX_TEXT = 30000  # 提取正文上限（registry 仍会截到 4000，此处为 fetch 细节用）

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/126.0 Safari/537.36 cof-research/1.6"}

_TAG_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.S | re.I)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _is_public_url(url: str) -> str | None:
    """校验 URL 合法且指向公网；返回错误原因（None=通过）。"""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "仅支持 http/https 链接"
    host = parsed.hostname or ""
    if not host:
        return "URL 缺少主机名"
    if host in ("localhost",) or host.endswith(".local"):
        return "拒绝访问本机地址"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass  # 域名：解析后再查一次
    else:
        if ip.is_private or ip.is_loopback or ip.is_link_local or \
                ip.is_reserved or ip.is_multicast:
            return "拒绝访问内网/保留地址"
        return None
    try:
        for info in socket.getaddrinfo(host, None):
            addr = info[4][0]
            ipa = ipaddress.ip_address(addr)
            if not (ipa.is_private or ipa.is_loopback or ipa.is_link_local
                    or ipa.is_reserved or ipa.is_multicast):
                return None
        return "解析到的地址均为内网地址，拒绝访问"
    except socket.gaierror:
        return f"域名解析失败：{host}"


def _extract_text(html: str) -> str:
    """trafilatura 优先；缺依赖时降级为标签剥离（保底可用）。"""
    try:
        import trafilatura  # noqa: PLC0415
    except ImportError:
        trafilatura = None
    if trafilatura is not None:
        try:
            text = trafilatura.extract(html, include_comments=False,
                                       include_tables=False)
            if text:
                return re.sub(r"\n{3,}", "\n\n", text).strip()
        except Exception:
            pass
    body = _TAG_RE.sub(" ", html)
    body = _HTML_TAG_RE.sub(" ", body)
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n", body)
    return body.strip()


def fetch_page(url: str) -> dict:
    """抓取网页正文。返回 {text, details, is_error}，不抛异常。"""
    url = (url or "").strip()
    if not url:
        return {"text": "fetch_page 参数错误：url 不能为空",
                "details": {}, "is_error": True}
    err = _is_public_url(url)
    if err:
        return {"text": f"fetch_page 拒绝访问：{err}",
                "details": {"url": url}, "is_error": True}
    try:
        resp = requests.get(url, headers=_UA, timeout=TIMEOUT, stream=True)
        resp.raise_for_status()
        content = resp.raw.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            return {"text": f"fetch_page 放弃：页面超过 {MAX_BYTES // (1024*1024)}MB 上限",
                    "details": {"url": url}, "is_error": True}
        ctype = resp.headers.get("Content-Type", "")
        if "html" not in ctype.lower() and "text" not in ctype.lower():
            return {"text": f"fetch_page 放弃：不是网页/文本（{ctype}），"
                            "可换用可读的 HTML 链接",
                    "details": {"url": url}, "is_error": True}
        resp.encoding = resp.encoding or "utf-8"
        text = _extract_text(resp.text)
    except requests.Timeout:
        return {"text": f"fetch_page 超时（>{TIMEOUT}s）：{url}",
                "details": {"url": url}, "is_error": True}
    except requests.HTTPError as exc:
        return {"text": f"fetch_page 失败：HTTP "
                        f"{exc.response.status_code if exc.response else '?'}（{url}）",
                "details": {"url": url}, "is_error": True}
    except requests.RequestException as exc:
        return {"text": f"fetch_page 失败：{type(exc).__name__}（{url}）",
                "details": {"url": url}, "is_error": True}
    if not text:
        return {"text": "fetch_page 未提取到正文（可能是纯 JS 渲染页面），"
                        "可换用其他链接或搜索引擎摘要",
                "details": {"url": url}, "is_error": False}
    title = ""
    try:
        import trafilatura  # noqa: PLC0415
        meta = trafilatura.extract_metadata(resp.text) if resp.text else None
        title = (meta.title or "") if meta else ""
    except Exception:
        title = ""
    text = text[:_MAX_TEXT]
    return {
        "text": f"网页正文（{url}）：\n{text}",
        "details": {"url": url, "title": title or "",
                    "length": len(text)},
        "is_error": False,
    }
