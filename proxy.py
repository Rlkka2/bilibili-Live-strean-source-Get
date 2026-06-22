"""
B站直播流本地代理模块
使用短token映射替代base64编码URL，避免URL过长导致播放器拒绝
"""

import http.server
import socketserver
import threading
import uuid
import socket
from urllib.parse import urljoin

import requests

from bilibili_client import UA


class BiliStreamProxy:
    """本地HTTP代理：用短token映射B站CDN真实URL，自动附加Referer"""

    def __init__(self, referer: str = "https://live.bilibili.com/") -> None:
        self.referer = referer
        self.port = self._find_free_port()
        self._url_map: dict[str, str] = {}  # token → real_url
        self._server: socketserver.TCPServer | None = None
        self._thread: threading.Thread | None = None

    @staticmethod
    def _find_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    # ==================== 服务器 ====================

    def start(self) -> None:
        proxy_ref = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self_h):
                try:
                    token = self_h.path.lstrip("/").split("?")[0]
                    real_url = proxy_ref._url_map.get(token)

                    if not real_url:
                        self_h.send_response(404)
                        self_h.end_headers()
                        self_h.wfile.write(b"Unknown token")
                        return

                    print(f"[Proxy] → {real_url[:100]}...")

                    resp = requests.get(
                        real_url,
                        headers={
                            "User-Agent": UA,
                            "Referer": proxy_ref.referer,
                        },
                        timeout=30,
                    )

                    body = resp.content
                    ct = resp.headers.get("Content-Type", "")

                    # m3u8 文件：把内部URL（含相对URL）全替换为代理token
                    if "mpegurl" in ct or real_url.endswith(".m3u8"):
                        body = proxy_ref._rewrite_m3u8(body, real_url)
                        ct = "application/vnd.apple.mpegurl"

                    self_h.send_response(200)
                    self_h.send_header("Content-Type", ct)
                    self_h.send_header("Content-Length", len(body))
                    self_h.send_header("Access-Control-Allow-Origin", "*")
                    self_h.end_headers()
                    self_h.wfile.write(body)

                except Exception as e:
                    print(f"[Proxy ERROR] {e}")
                    self_h.send_response(502)
                    self_h.end_headers()
                    self_h.wfile.write(f"Proxy error: {e}".encode())

            def log_message(self_h, *args):
                pass

        self._server = socketserver.ThreadingTCPServer(
            ("127.0.0.1", self.port), Handler
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()
        print(f"[Proxy] 启动于 127.0.0.1:{self.port}")

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server = None
            print("[Proxy] 已停止")

    # ==================== URL 映射 ====================

    def wrap_url(self, real_url: str) -> str:
        """为真实URL生成短token，返回本地代理URL"""
        token = uuid.uuid4().hex[:12]
        self._url_map[token] = real_url
        return f"http://127.0.0.1:{self.port}/{token}"

    # ==================== M3U8 改写 ====================

    def _rewrite_m3u8(self, content: bytes, base_url: str) -> bytes:
        """将m3u8内的片段URL（含相对路径）替换为本地代理token URL"""
        text = content.decode("utf-8", errors="replace")
        lines = text.splitlines()
        out = []

        for line in lines:
            s = line.strip()
            if s and not s.startswith("#"):
                # 将相对URL绝对化（1782065739.m4s → https://cdn.../1782065739.m4s）
                absolute = urljoin(base_url, s)
                out.append(self.wrap_url(absolute))
            else:
                out.append(line)

        result = "\n".join(out)
        print(f"[Proxy] 改写m3u8: {len(lines)}行 → {len(out)}行")
        return result.encode()
