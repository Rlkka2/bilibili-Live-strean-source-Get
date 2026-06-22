import requests
import time
import qrcode
from io import BytesIO
from PIL import Image
from urllib.parse import urlparse, parse_qs

# 全局 UA
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
SESSDATA = ""

def get_qrcode():
    """获取登录二维码链接 + qrcode_key"""
    url = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
    resp = requests.get(url, headers={"User-Agent": UA})
    data = resp.json()
    qr_url = data["data"]["url"]
    qr_key = data["data"]["qrcode_key"]
    return qr_url, qr_key

def show_qr_in_console(qr_link):
    """控制台打印二维码"""
    qr = qrcode.QRCode()
    qr.add_data(qr_link)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    pil_img = Image.open(buf)
    pil_img.show()
    print("二维码已弹出图片窗口，请用 B站APP 扫码登录")

def poll_login(qrcode_key):
    """轮询扫码状态，成功返回 SESSDATA"""
    global SESSDATA
    poll_url = f"https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key={qrcode_key}"
    print("等待扫码确认...")
    while True:
        resp = requests.get(poll_url, headers={"User-Agent": UA})
        res = resp.json()
        # 修复：B站API现在返回两层code，真正的扫码状态在 data.code 中
        data = res.get("data", {})
        code = data.get("code") if isinstance(data, dict) else res.get("code")
        if code == 0 and data.get("url"):
            # 登录成功，提取 SESSDATA
            jump_url = data["url"]
            params = parse_qs(urlparse(jump_url).query)
            SESSDATA = params.get("SESSDATA", [""])[0]
            print(f"✅ 登录成功！SESSDATA={SESSDATA[:30]}...")
            return
        elif code == 86038:
            print("❌ 二维码已过期，请重新运行程序")
            exit()
        elif code == 86090:
            print("⏳ 已扫码，等待确认...")
        elif code == 86101:
            print("⏳ 等待扫码...")
        time.sleep(1.8)

def get_live_m3u8(room_id: str):
    """根据房间号获取 H.264 m3u8 直播源"""
    if not SESSDATA:
        print("请先完成扫码登录！")
        return
    headers = {
        "User-Agent": UA,
        "Referer": f"https://live.bilibili.com/{room_id}",
        "Cookie": f"SESSDATA={SESSDATA}"
    }
    params = {
        "room_id": room_id,
        "protocol": "0,1",
        "format": "0,1,2",
        "codec": "0",    # 只拿 h264 兼容流，避开 av1 报错
        "qn": 10000,
        "platform": "web",
        "ptype": 8
    }
    api = "https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo"
    resp = requests.get(api, headers=headers, params=params)
    data = resp.json()
    play_info = data["data"]["playurl_info"]["playurl"]
    # 画质描述
    QN_NAMES = {30000:"杜比",20000:"4K",15000:"2K",10000:"原画",400:"蓝光",250:"超清",150:"高清",80:"流畅"}
    CODEC_NAMES = {"avc":"H.264","hevc":"H.265","av1":"AV1"}
    g_qn_desc = {}
    for d in play_info.get("g_qn_desc", []):
        g_qn_desc[d.get("qn",0)] = d.get("desc","")

    streams = []

    for stream in play_info["stream"]:
        if stream["protocol_name"] != "http_hls":
            continue
        for fmt in stream["format"]:
            for codec in fmt["codec"]:
                # qn在codec层级
                qn = codec.get("current_qn") or 0
                qn_desc = g_qn_desc.get(qn) or QN_NAMES.get(qn, f"画质{qn}")
                codec_name = CODEC_NAMES.get(codec.get("codec_name",""), codec.get("codec_name",""))
                format_name = fmt.get("format_name","")
                # base_url 在 codec 层级（新API结构），不在 url_info 中
                base_url = codec.get("base_url", "").rstrip("?")
                for url_info in codec["url_info"]:
                    host = url_info.get("host", "")
                    extra = url_info.get("extra", "")
                    if not host.startswith("http"):
                        host = "https:" + host
                    full_url = host + base_url
                    if extra:
                        full_url += "?" + extra.lstrip("?&")
                    streams.append((qn, qn_desc, codec_name, format_name, full_url))

    if not streams:
        print("未获取到直播流：房间未开播 / 无原画权限")
        return
    print("\n===== 可用 M3U8 直播源 =====\n")
    for idx, (qn, qn_desc, codec_name, format_name, link) in enumerate(streams, 1):
        print(f"{idx}. [{qn_desc}] [{codec_name}] [{format_name}]\n   {link}\n")

if __name__ == "__main__":
    print("===== B站直播源提取工具 Python版 =====")
    qr_url, qr_key = get_qrcode()
    show_qr_in_console(qr_url)
    poll_login(qr_key)
    while True:
        rid = input("\n请输入直播间数字ID（输入 q 退出）：").strip()
        if rid.lower() == "q":
            break
        if not rid.isdigit():
            print("房间号必须是纯数字！")
            continue
        get_live_m3u8(rid)