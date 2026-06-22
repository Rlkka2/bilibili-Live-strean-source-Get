"""
B站直播源提取工具 — pywebview 图形界面
"""

import sys
import io
import os
import base64
import threading
import subprocess
from typing import Optional, Any

import webview
import qrcode

from bilibili_client import BilibiliClient, COOKIE_FILE
from proxy import BiliStreamProxy


# ===== 日志捕获 =====
class LogBuffer:
    """捕获 stdout/stderr + 程序日志到一个可查询的缓冲区"""

    def __init__(self, max_lines: int = 2000) -> None:
        self._lines: list[str] = []
        self._max_lines = max_lines

    def write(self, text: str) -> None:
        for line in text.splitlines():
            if line:
                self._lines.append(line)
        if len(self._lines) > self._max_lines:
            self._lines = self._lines[-self._max_lines:]

    def flush(self) -> None:
        pass

    def get_logs(self) -> str:
        return "\n".join(self._lines)

    def clear(self) -> None:
        self._lines.clear()


LOG = LogBuffer()
# 重定向 stdout/stderr 到日志缓冲区
sys.stdout = LOG
sys.stderr = LOG


# ===== API 桥接类 =====
class BiliApi:
    """暴露给 JavaScript 的 API"""

    def __init__(self) -> None:
        self.client = BilibiliClient()
        self._proxy: Optional[BiliStreamProxy] = None
        self._fetch_status = "idle"
        self._fetch_result: Any = None
        self._fetch_progress = ""

    # ==================== 日志 ====================

    def get_logs(self) -> str:
        return LOG.get_logs()

    def clear_logs(self) -> None:
        LOG.clear()
        print("[日志] 已清空")

    # ==================== QR 登录 ====================

    def generate_qr(self) -> dict:
        print("[登录] 获取二维码...")
        return self.client.generate_qr()

    def make_qr_image(self, qr_url: str) -> str:
        """生成二维码 PNG，返回 base64"""
        qr = qrcode.QRCode(box_size=4, border=2)
        qr.add_data(qr_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()

    def poll_qr(self, qrcode_key: str) -> dict:
        return self.client.poll_qr(qrcode_key)

    def get_login_info(self) -> dict:
        return {
            "is_logged_in": self.client.is_logged_in,
            "uid": self.client.uid,
            "nickname": self.client.nickname,
        }

    def save_cookies(self) -> None:
        self.client.save_cookies(COOKIE_FILE)
        print("[登录] Cookie 已保存")

    def load_cookies(self) -> dict | None:
        if self.client.load_cookies(COOKIE_FILE):
            print(f"[登录] 自动登录成功 — {self.client.nickname}")
            return self.get_login_info()
        print("[登录] 未找到有效 Cookie，请扫码登录")
        return None

    def logout(self) -> None:
        self.client.clear_cookies(COOKIE_FILE)
        print("[登录] 已退出登录")

    # ==================== 直播源获取 ====================

    def start_fetch_streams(self, room_id: str) -> None:
        """异步开始获取直播源（后台线程）"""
        self._fetch_status = "running"
        self._fetch_result = None
        self._fetch_progress = "正在获取房间信息..."

        def on_progress(msg: str) -> None:
            self._fetch_progress = msg

        def worker() -> None:
            try:
                room_info = self.client.get_room_info(room_id)
                if not room_info["success"]:
                    self._fetch_result = {"error": room_info["error"]}
                    self._fetch_status = "error"
                    return

                if not room_info.get("is_live"):
                    self._fetch_result = {"room_info": room_info, "play_info": {"success": False, "error": "房间未开播"}}
                    self._fetch_status = "done"
                    return

                self._fetch_progress = "正在获取直播流..."
                play_info = self.client.get_play_info(room_id, progress_callback=on_progress)

                self._fetch_result = {"room_info": room_info, "play_info": play_info}
                self._fetch_status = "done"

            except Exception as e:
                self._fetch_result = {"error": str(e)}
                self._fetch_status = "error"

        threading.Thread(target=worker, daemon=True).start()

    def get_fetch_status(self) -> dict:
        return {
            "status": self._fetch_status,
            "progress": self._fetch_progress,
            "result": self._fetch_result,
        }

    # ==================== 播放 ====================

    def play(self, url: str, room_id: str) -> dict:
        """启动本地代理并打开播放器"""
        referer = f"https://live.bilibili.com/{room_id}"
        if self._proxy is None:
            self._proxy = BiliStreamProxy(referer=referer)
            self._proxy.start()
        else:
            self._proxy.referer = referer

        local = self._proxy.wrap_url(url)
        print(f"[播放] 代理URL: {local}")

        player = self._find_player()
        opened = False
        if player:
            try:
                subprocess.Popen([player, local])
                opened = True
                print(f"[播放] 已启动: {os.path.basename(player)}")
            except Exception as e:
                print(f"[播放] 启动失败: {e}")

        if not opened and sys.platform == "win32":
            try:
                os.startfile(local)
                opened = True
            except Exception:
                pass

        if opened:
            return {"status": "已启动播放器", "url": local}
        else:
            return {"status": "未找到播放器，请手动打开: " + local, "url": local}

    @staticmethod
    def _find_player() -> str:
        import shutil
        candidates = [
            r"C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe",
            r"C:\Program Files (x86)\DAUM\PotPlayer\PotPlayerMini64.exe",
            r"D:\PotPlayer\PotPlayerMini64.exe",
            r"D:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe",
            r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        ]
        for name in ["PotPlayerMini64.exe", "PotPlayerMini.exe", "vlc.exe"]:
            found = shutil.which(name)
            if found:
                return found
        for path in candidates:
            if os.path.exists(path):
                return path
        return ""

    # ==================== 窗口控制 ====================

    def minimize_window(self) -> None:
        if webview.windows:
            webview.windows[0].minimize()

    def toggle_maximize(self) -> None:
        if webview.windows:
            webview.windows[0].toggle_fullscreen()

    def close_window(self) -> None:
        # 清理代理
        if self._proxy:
            self._proxy.stop()
        if webview.windows:
            webview.windows[0].destroy()

    # ==================== 剪贴板 ====================

    def copy(self, text: str) -> None:
        """将文本复制到系统剪贴板（Win=PowerShell Base64, macOS=pbcopy, Linux=xclip）"""
        try:
            if sys.platform == "win32":
                self._win32_copy(text)
            elif sys.platform == "darwin":
                subprocess.run(["pbcopy"], input=text, text=True, timeout=5)
            else:
                subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=text, text=True, timeout=5,
                )
            print(f"[剪贴板] 已复制 ({len(text)} 字符)")
        except Exception as e:
            print(f"[剪贴板] 复制失败: {e}")

    @staticmethod
    def _win32_copy(text: str) -> None:
        """Windows 剪贴板写入：Base64 → PowerShell → Set-Clipboard
        不在 PowerShell 命令行中直接传文本，避免任何转义问题。"""
        import base64
        import subprocess

        # UTF-8 → Base64，只含 A-Za-z0-9+/= 无需转义
        b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
        ps_cmd = (
            "[System.Text.Encoding]::UTF8.GetString("
            f"[System.Convert]::FromBase64String('{b64}')) "
            "| Set-Clipboard"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            timeout=5,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )


# ===== 入口 =====
def main() -> None:
    api = BiliApi()
    html_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")

    webview.create_window(
        title="B站直播源提取工具",
        url=html_path,
        js_api=api,
        width=960,
        height=680,
        min_size=(800, 520),
        frameless=True,
        easy_drag=True,
    )

    print("[系统] 应用启动")
    webview.start()


if __name__ == "__main__":
    main()
