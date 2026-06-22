"""
B站直播源提取 - API客户端模块
功能：二维码登录、Cookie管理、直播间信息查询、直播流获取
"""

import requests
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, Optional, List

# ===== 常量 =====
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

COOKIE_FILE = "cookies.json"

# 画质描述映射（从高到低）
QN_NAMES = {
    30000: "杜比",
    20000: "4K",
    15000: "2K",
    10000: "原画",
    400: "蓝光",
    250: "超清",
    150: "高清",
    80: "流畅",
}

# 编码名称映射
CODEC_NAMES = {
    "avc": "H.264 (AVC)",
    "hevc": "H.265 (HEVC)",
    "av1": "AV1",
}


class BilibiliClient:
    """B站直播API客户端，封装登录、房间查询、流获取等功能"""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA})
        self.is_logged_in: bool = False
        self.uid: Optional[int] = None
        self.nickname: str = ""

    # ==================== 二维码登录 ====================

    def generate_qr(self) -> Dict[str, Any]:
        """
        获取登录二维码URL和qrcode_key
        注意：QR登录流程不使用Session，直接发请求（与原版1.py一致），
              避免Session中可能存在的多余Cookie干扰扫码关联。
        返回: {"success": bool, "qr_url": str, "qrcode_key": str} | {"success": False, "error": str}
        """
        try:
            url = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
            resp = requests.get(url, headers={"User-Agent": UA}, timeout=15)
            data = resp.json()
            if data.get("code") != 0:
                return {"success": False, "error": data.get("message", "获取二维码失败")}
            return {
                "success": True,
                "qr_url": data["data"]["url"],
                "qrcode_key": data["data"]["qrcode_key"],
            }
        except requests.RequestException as e:
            return {"success": False, "error": f"网络错误: {e}"}
        except (KeyError, ValueError) as e:
            return {"success": False, "error": f"响应解析失败: {e}"}

    def poll_qr(self, qrcode_key: str) -> Dict[str, Any]:
        """
        轮询扫码状态（每次调用只请求一次，不阻塞循环）
        注意：QR登录流程不使用Session，直接发请求（与原版1.py一致）。
              登录成功后手动将需要的Cookie写入Session供后续API使用。
        返回:
          - {"success": True, "status": "logged_in", "message": str}  登录成功
          - {"success": True, "status": "scanned", "message": str}     已扫码待确认
          - {"success": True, "status": "waiting", "message": str}     等待扫码
          - {"success": False, "status": "expired", "message": str}    二维码过期
          - {"success": False, "status": "error", "message": str}      其他错误
        """
        try:
            poll_url = (
                "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
                f"?qrcode_key={qrcode_key}"
            )
            resp = requests.get(poll_url, headers={"User-Agent": UA}, timeout=15)
            res = resp.json()

            # B站API返回两层结构：顶层code始终为0，真正的扫码状态在 data.code 中
            # data.code = 0      → 登录成功（data.url非空）
            # data.code = 86101  → 等待扫码
            # data.code = 86090  → 已扫码待确认
            # data.code = 86038  → 二维码过期
            data = res.get("data", {})
            raw_code = data.get("code") if isinstance(data, dict) else None
            try:
                status_code = int(raw_code) if raw_code is not None else None
            except (TypeError, ValueError):
                status_code = None

            # 调试日志
            print(f"[Poll] raw_code={raw_code} status_code={status_code} "
                  f"has_url={bool(data.get('url'))} "
                  f"msg={data.get('message', res.get('message',''))}")

            if status_code == 0 and data.get("url"):
                # 登录成功 → 跟随跳转URL，手动提取Cookie写入Session
                try:
                    jump_url = data["url"]
                    from urllib.parse import urlparse, parse_qs
                    parsed = urlparse(jump_url)
                    params = parse_qs(parsed.query)
                    sessdata = params.get("SESSDATA", [None])[0]
                    bili_jct = params.get("bili_jct", [None])[0]
                    if sessdata:
                        self.session.cookies.set("SESSDATA", sessdata,
                                                  domain=".bilibili.com", path="/")
                    if bili_jct:
                        self.session.cookies.set("bili_jct", bili_jct,
                                                  domain=".bilibili.com", path="/")
                    # 跟随跳转URL捕获完整Set-Cookie
                    self.session.get(jump_url, timeout=10, allow_redirects=True)
                except Exception as e:
                    print(f"[Poll] Cookie设置异常: {e}")
                self.is_logged_in = True
                self._extract_user_from_cookies()
                return {"success": True, "status": "logged_in", "message": "登录成功"}
            elif status_code == 86038:
                return {"success": False, "status": "expired", "message": "二维码已过期"}
            elif status_code == 86090:
                return {"success": True, "status": "scanned", "message": "已扫码，请在手机上确认"}
            elif status_code == 86101:
                return {"success": True, "status": "waiting", "message": "等待扫码中..."}
            else:
                msg = data.get("message", res.get("message", f"未知状态码: {status_code}"))
                return {"success": False, "status": "error", "message": msg}
        except requests.RequestException as e:
            return {"success": False, "status": "error", "message": f"网络错误: {e}"}
        except (KeyError, ValueError) as e:
            return {"success": False, "status": "error", "message": f"解析失败: {e}"}

    def _extract_user_from_cookies(self) -> None:
        """从session cookies中提取uid，并调用nav接口获取昵称"""
        # 方法1：直接从cookies提取DedeUserID
        for cookie in self.session.cookies:
            if cookie.name == "DedeUserID":
                try:
                    self.uid = int(cookie.value)
                except (ValueError, TypeError):
                    pass
                break
        # 方法2：通过nav接口获取更完整的用户信息（同时验证登录有效性）
        try:
            resp = self.session.get(
                "https://api.bilibili.com/x/web-interface/nav", timeout=10
            )
            data = resp.json()
            if data.get("code") == 0:
                user = data["data"]
                if user.get("isLogin"):
                    self.uid = user.get("mid", self.uid)
                    self.nickname = user.get("uname", "")
                    self.is_logged_in = True
                else:
                    self.is_logged_in = False
        except Exception:
            pass  # nav接口失败不影响从cookies提取的uid

    # ==================== Cookie 持久化 ====================

    def save_cookies(self, path: str = COOKIE_FILE) -> bool:
        """保存cookies到JSON文件"""
        try:
            cookies = requests.utils.dict_from_cookiejar(self.session.cookies)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
            return True
        except OSError as e:
            print(f"[Cookie保存失败] {e}")
            return False

    def load_cookies(self, path: str = COOKIE_FILE) -> bool:
        """
        从JSON文件加载cookies并验证是否有效
        返回: True=加载成功且有效, False=失败或已过期
        """
        if not os.path.exists(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            requests.utils.cookiejar_from_dict(cookies, self.session.cookies)

            # 验证cookies是否仍然有效
            resp = self.session.get(
                "https://api.bilibili.com/x/web-interface/nav", timeout=10
            )
            data = resp.json()
            if data.get("code") == 0 and data["data"].get("isLogin"):
                self.is_logged_in = True
                self.uid = data["data"].get("mid")
                self.nickname = data["data"].get("uname", "")
                return True
            # 登录已过期，清除无效cookie文件
            os.remove(path)
            return False
        except (OSError, json.JSONDecodeError, requests.RequestException) as e:
            print(f"[Cookie加载失败] {e}")
            return False

    def clear_cookies(self, path: str = COOKIE_FILE) -> None:
        """清除登录状态和本地cookie文件"""
        self.session.cookies.clear()
        self.is_logged_in = False
        self.uid = None
        self.nickname = ""
        if os.path.exists(path):
            os.remove(path)

    # ==================== 直播间信息 ====================

    def get_room_info(self, room_id: str) -> Dict[str, Any]:
        """
        获取直播间基本信息（标题、主播、开播状态等）
        先调 room_init 获取基础数据，再调 get_info 补全标题和分区。
        返回: {"success": bool, ...}
        """
        try:
            # 第一步：获取房间基础信息（开播状态、room_id等）
            init_url = "https://api.live.bilibili.com/room/v1/Room/room_init"
            resp = self.session.get(init_url, params={"id": room_id}, timeout=10)
            data = resp.json()
            if data.get("code") != 0:
                return {
                    "success": False,
                    "error": data.get("message", "获取房间信息失败"),
                }

            room = data["data"]
            live_status = room.get("live_status", 0)
            real_room_id = room.get("room_id", int(room_id))

            # 第二步：获取详细信息（标题、分区、主播名等）
            title = ""
            area_name = ""
            parent_area_name = ""
            uname = ""
            try:
                info_url = "https://api.live.bilibili.com/room/v1/Room/get_info"
                info_resp = self.session.get(info_url, params={"room_id": real_room_id}, timeout=10)
                info_data = info_resp.json()
                if info_data.get("code") == 0:
                    room_info = info_data["data"]
                    title = room_info.get("title", "")
                    area_name = room_info.get("area_name", "")
                    parent_area_name = room_info.get("parent_area_name", "")
                    uname = room_info.get("uname", "")
            except Exception:
                pass  # 详细信息接口失败不影响基础数据返回

            return {
                "success": True,
                "room_id": real_room_id,
                "short_id": room.get("short_id", 0),
                "live_status": live_status,
                "is_live": live_status == 1,
                "title": title,
                "uid": room.get("uid", 0),
                "uname": uname,
                "area_name": area_name,
                "parent_area_name": parent_area_name,
            }
        except requests.RequestException as e:
            return {"success": False, "error": f"网络错误: {e}"}
        except (KeyError, ValueError) as e:
            return {"success": False, "error": f"数据解析失败: {e}"}

    # ==================== 直播流获取 ====================

    def get_play_info(self, room_id: str, max_qn: int = 10000, progress_callback=None) -> Dict[str, Any]:
        """
        获取直播间HLS(.m3u8)直播流地址列表
        参数:
          - room_id: 直播间数字ID
          - max_qn: 最高请求画质 (10000=原画, 400=蓝光, 250=超清, 150=高清, 80=流畅)
          - progress_callback: 测速进度回调 fn(msg: str)
        返回: {"success": bool, "streams": [{"protocol","format","codec","qn_desc","url","speed_ms","speed_rounds","speed_total_rounds"},...]}
        """
        if not self.is_logged_in:
            return {"success": False, "error": "请先登录后再获取直播源"}

        try:
            params = {
                "room_id": room_id,
                "protocol": "0,1",
                "format": "0,1,2",
                "codec": "0,1",  # 0=H.264(AVC), 1=H.265(HEVC)
                "qn": max_qn,
                "platform": "web",
                "ptype": 8,
            }
            headers = {"Referer": f"https://live.bilibili.com/{room_id}"}

            api_url = "https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo"
            resp = self.session.get(api_url, headers=headers, params=params, timeout=15)
            data = resp.json()

            if data.get("code") != 0:
                return {"success": False, "error": data.get("message", "获取播放信息失败")}

            playurl = data["data"]["playurl_info"]["playurl"]

            # 构建画质描述映射（从API返回的g_qn_desc）
            qn_descriptions: Dict[int, str] = {}
            for desc in playurl.get("g_qn_desc", []):
                qn = desc.get("qn", 0)
                desc_text = desc.get("desc", "")
                qn_descriptions[qn] = desc_text

            streams: List[Dict[str, str]] = []

            for stream in playurl.get("stream", []):
                protocol_name = stream.get("protocol_name", "")
                # 只取HLS流（.m3u8），跳过FLV和其他协议
                if protocol_name != "http_hls":
                    continue

                for fmt in stream.get("format", []):
                    format_name = fmt.get("format_name", "")

                    for codec_info in fmt.get("codec", []):
                        # qn在codec层级（current_qn），不在format层级
                        stream_qn = codec_info.get("current_qn") or fmt.get("qn") or 0
                        qn_label = qn_descriptions.get(stream_qn, QN_NAMES.get(stream_qn, f"画质{stream_qn}"))
                        codec_name = codec_info.get("codec_name", "")
                        codec_display = CODEC_NAMES.get(codec_name, codec_name)

                        # base_url 在 codec_info 层级（新API结构）
                        base_url = codec_info.get("base_url", "")

                        for url_info in codec_info.get("url_info", []):
                            host = url_info.get("host", "")
                            extra = url_info.get("extra", "")

                            # 确保host以https://开头
                            if not host.startswith("http"):
                                host = "https:" + host

                            # 拼接URL：host + base_url + extra
                            # base_url可能以?结尾，extra不含前导?/&
                            bu_clean = base_url.rstrip("?")
                            full_url = host + bu_clean
                            if extra:
                                full_url += "?" + extra.lstrip("?&")

                            streams.append({
                                "protocol": protocol_name,
                                "format": format_name,
                                "codec": codec_display,
                                "codec_raw": codec_name,
                                "qn": stream_qn,
                                "qn_desc": qn_label,
                                "url": full_url,
                            })

            if not streams:
                return {"success": False, "error": "未获取到直播流：房间未开播或无可用流"}

            # 按画质从高到低排序
            streams.sort(key=lambda s: s["qn"], reverse=True)

            # 并发多次测速过滤：只保留能连通的流（含延迟数据）
            referer = f"https://live.bilibili.com/{room_id}"
            streams = self._filter_reachable(streams, referer, progress_callback=progress_callback)

            if not streams:
                return {"success": False, "error": "所有直播流均无法连通，可能是CDN节点不可达"}

            return {"success": True, "streams": streams}

        except requests.RequestException as e:
            return {"success": False, "error": f"网络错误: {e}"}
        except (KeyError, TypeError, ValueError) as e:
            return {"success": False, "error": f"数据解析失败: {e}"}

    @staticmethod
    def _filter_reachable(
        streams: List[Dict],
        referer: str,
        test_rounds: int = 3,
        progress_callback=None,
    ) -> List[Dict]:
        """并发多次测速：每个CDN节点测 N 轮，取多数判据 + 记录平均延迟"""
        if not streams:
            return streams

        unique_urls = list({s["url"] for s in streams})
        print(f"[测速] 检测 {len(unique_urls)} 个CDN节点 × {test_rounds} 轮...")

        # 每个URL N轮测试结果: [{url: (ok, latency_ms), ...}, ...]
        all_results: Dict[str, list] = {url: [] for url in unique_urls}

        for round_idx in range(1, test_rounds + 1):
            msg = f"正在第 {round_idx}/{test_rounds} 轮测速..."
            print(f"[测速] {msg}")
            if progress_callback:
                progress_callback(msg)

            # 本轮只测尚未被判定"确定不可达"的节点
            # 但全部测以获取完整延迟数据
            round_urls = unique_urls  # 全量测试

            results: Dict[str, bool] = {}
            with ThreadPoolExecutor(max_workers=min(10, len(unique_urls))) as executor:
                futures = {
                    executor.submit(
                        BilibiliClient._test_one_url, u, referer
                    ): u for u in round_urls
                }
                for future in as_completed(futures):
                    url = futures[future]
                    try:
                        ok, latency = future.result()
                        results[url] = ok
                        all_results[url].append((ok, latency))
                    except Exception:
                        results[url] = False
                        all_results[url].append((False, 0))

            ok_count = sum(1 for v in results.values() if v)
            if progress_callback:
                progress_callback(
                    f"第 {round_idx}/{test_rounds} 轮完成 — 连通 {ok_count}/{len(unique_urls)}"
                )

        # 综合判定：至少2轮通过才算可达
        reachable: Dict[str, bool] = {}
        latency_map: Dict[str, float] = {}
        for url, rounds in all_results.items():
            ok_count = sum(1 for ok, _ in rounds if ok)
            reachable[url] = ok_count >= 2  # 3轮中至少2轮通过
            latencies = [lat for ok, lat in rounds if ok and lat > 0]
            latency_map[url] = round(sum(latencies) / len(latencies)) if latencies else 0

        passed = sum(1 for v in reachable.values() if v)
        print(f"[测速] 最终连通: {passed}/{len(unique_urls)} (≥2/3通过)")

        # 过滤 + 附加测速信息
        working: List[Dict] = []
        for s in streams:
            url = s["url"]
            if reachable.get(url, False):
                s = dict(s)  # 浅拷贝，避免影响原始数据
                s["speed_ms"] = latency_map.get(url, 0)
                s["speed_rounds"] = sum(1 for ok, _ in all_results.get(url, []) if ok)
                s["speed_total_rounds"] = test_rounds
                working.append(s)

        skipped = len(streams) - len(working)
        if skipped:
            print(f"[测速] 已过滤 {skipped} 条不可达流")

        # 按延迟升序排列（快的排前面）
        working.sort(key=lambda s: s.get("speed_ms", 9999))
        return working

    @staticmethod
    def _test_one_url(url: str, referer: str):
        """测试单个URL连通性，返回 (成功, 延迟ms) 元组"""
        t0 = time.time()
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": UA, "Referer": referer},
                timeout=3,
                stream=True,
            )
            chunk = next(resp.iter_content(1), None)
            resp.close()
            latency = int((time.time() - t0) * 1000)
            ok = resp.status_code == 200 and chunk is not None
            return ok, latency
        except Exception:
            latency = int((time.time() - t0) * 1000)
            return False, latency
