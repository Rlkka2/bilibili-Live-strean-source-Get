# 🎬 B站直播源提取工具

> 基于 pywebview 的桌面端 B站直播源提取器，支持扫码登录、多画质直播流获取、CDN 测速筛选、一键播放。

<p align="center">
  <img src="https://img.shields.io/badge/python-3.8+-blue" alt="Python">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey" alt="Platform">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
</p>

## ✨ 功能特性

- 🔐 **扫码登录** — 支持 B站 APP 扫码登录，Cookie 本地持久化，下次启动自动恢复
- 📺 **直播源提取** — 输入房间号即可获取 HLS (.m3u8) 直播流地址
- 🎯 **多画质支持** — 杜比 / 4K / 2K / 原画 / 蓝光 / 超清 / 高清 / 流畅，按画质从高到低排列
- 🧪 **CDN 测速筛选** — 并发多轮测速，自动过滤不可达节点，按延迟排序
- ▶️ **一键播放** — 自动检测 PotPlayer / VLC，调用本地播放器直接播放
- 🔗 **本地代理** — 自动附加 Referer 头 + 短 Token 映射，解决 CDN 防盗链和 URL 过长问题
- 🎨 **双主题** — 浅色 / 深色模式切换
- 📋 **日志面板** — 实时查看程序运行状态

## 🚀 快速开始

### 环境要求

- Python 3.8+
- Windows / macOS / Linux

### 安装依赖

```bash
pip install pywebview qrcode requests pillow
```

> Linux 用户可能需要额外安装 `python3-gi` 或 `python3-pyqt5`，详见 [pywebview 文档](https://pywebview.flowrl.com/guide/installation.html)。

### 运行

```bash
# 桌面版（推荐）
python main.py

# 命令行版（无 GUI，适合服务器 / 快速调试）
python 1.py
```

## 📖 使用说明

1. **登录** — 点击左侧「扫码登录」，用 B站 APP 扫描二维码
2. **获取直播源** — 输入直播间号（如 `6`），点击「获取直播源」或按回车
3. **播放** — 点击流地址旁的「播放」按钮，自动调用本地播放器
4. **复制** — 点击「复制」复制单条链接，或「复制全部」一键复制所有流

> 💡 播放器自动检测优先级：PotPlayer → VLC → 系统默认打开方式。直播流通过本地代理中转，自动处理 Referer 防盗链。

## 📁 项目结构

```
├── main.py              # 主入口 — pywebview 窗口 + JS API 桥接 + GUI 逻辑
├── bilibili_client.py   # B站 API 客户端 — 登录、房间查询、直播流获取、CDN 测速
├── proxy.py             # 本地 HTTP 代理 — Referer 注入 + m3u8 改写 + URL 短映射
├── 1.py                 # 命令行原型 — 无 GUI 依赖的简化版
├── templates/
│   └── index.html       # 前端界面（纯 HTML/CSS/JS，无框架依赖）
└── cookies.json         # 登录凭证持久化（运行后自动生成）
```

## 🛠 技术要点

| 模块 | 关键实现 |
|------|---------|
| **二维码登录** | 不使用 Session 自动捕获 Cookie，而是手动解析跳转 URL 中的 `SESSDATA` 再注入 Session，避免多余 Cookie 干扰扫码关联 |
| **直播流解析** | 解析 B站新 API 深层嵌套结构，注意 `base_url` 在 codec 层级而非 `url_info` 层级；URL 按 `host + base_url + extra` 三段拼接 |
| **CDN 测速** | `ThreadPoolExecutor` 并发检测，每个节点测 3 轮，至少 2 轮连通判定可达，3 秒超时 |
| **本地代理** | `ThreadingTCPServer` 实现，短 UUID Token 映射替代 base64 编码（URL 长度 ~30 字符 vs ~200+），自动改写 m3u8 内部分片引用 |
| **异步获取** | 直播源获取在后台线程执行，前端轮询状态，避免 GUI 冻结 |
| **剪贴板** | Windows 通过 Base64 → PowerShell → Set-Clipboard 安全写入，macOS 用 `pbcopy`，Linux 用 `xclip` |

## 📦 依赖说明

| 包 | 用途 |
|---|---|
| `pywebview` | 系统原生 WebView 桌面窗口容器 |
| `qrcode` + `Pillow` | 二维码图片生成 |
| `requests` | HTTP API 调用 + CDN 连通性检测 |
| `tkinter`（内置） | 剪贴板操作（Python 标准库） |

## ⚠️ 安全提示

账户 Cookie 以明文形式保存在本地 `cookies.json` 中。为确保账户安全，使用后请退出登录以清除本地凭证。

## 📄 License

MIT
