# picdav

基于 **WebDAV** 的图片上传与管理工具，支持图片浏览、上传、删除、代理访问，内置网易云音乐播放器、主题切换等扩展功能。

## 架构

提供两个独立入口，端口及监听地址通过 `picdav.yml` 配置，共用 WebDAV 配置（`config/image_uploader.json`，密码使用 Fernet 加密存储）：

| 服务 | 定位 | 说明 |
|------|------|------|
| **展示页** (`gallery.py`) | 纯浏览，适合公网暴露 | Masonry 布局、缩略图代理、随机图片、严格 URL 安全校验 |
| **管理端** (`upload.py`) | 完整功能，建议内网使用 | 上传/配置/图片库/删除 |

> 两者均内置 API Token 防护和音乐播放器。

## 功能对比

| 功能 | 展示页 (`gallery.py`) | 管理端 (`upload.py`) |
|------|:-:|:-:|
| 浏览图片 (Masonry) | ✓ | ✓ |
| 上传图片 | - | ✓ |
| 配置 WebDAV | - | ✓ |
| 删除图片 | - | ✓ |
| 图片代理（路径式，不暴露源站） | ✓ | ✓ |
| 缩略图生成 | ✓ | - |
| 随机图片跳转 | ✓ | - |
| 拖拽/粘贴上传 | - | ✓ |
| 网易云音乐播放器 | ✓ | ✓ |
| API Token 防护 | ✓ | ✓ |
| 日间/夜间/跟随系统主题切换 | ✓ | ✓ |
| 主题切换扩散动画 | ✓ | ✓ |
| FAB 自动隐藏到侧边栏 | ✓ | ✓ |

## 快速开始

```bash
# 1. 初始化应用配置（从示例复制，按需修改）
cp picdav.yml.example picdav.yml

# 2. 创建虚拟环境并安装依赖
uv sync

# 3. 终端 1：展示页 (端口通过 picdav.yml 配置，默认 5000)
uv run python gallery.py

# 4. 终端 2：管理端 (端口通过 picdav.yml 配置，默认 5001)
uv run python upload.py
```

### 使用 pip

```bash
pip install -r requirements.txt

# 终端 1：展示页
python gallery.py

# 终端 2：管理端
python upload.py
```

## 配置

### 应用配置（`picdav.yml`）

服务监听地址和端口、音乐播放器默认歌单、Umami 统计等在 `picdav.yml` 中配置（从 `picdav.yml.example` 复制后按需修改）：

```yaml
server:
  upload:      # 管理端 (upload.py)
    host: "127.0.0.1"
    port: 5001
  gallery:     # 展示页 (gallery.py)
    host: "127.0.0.1"
    port: 5000

webdav:
  # WebDAV 服务器配置（完整 URL，含远程路径）
  server_url: "http://example.com/remote.php/dav/files/user/Images"
  username: ""
  password: ""

theme:
  # 定时切换日间/夜间主题（留空禁用，24h 格式 "HH:MM"）
  dark_at: ""
  light_at: ""
```

### WebDAV 配置（`picdav.yml` webdav 段）

在管理端页面中填写 WebDAV 信息，或直接编辑 `picdav.yml` 中的 `webdav` 段：

- **服务器地址** — WebDAV 完整 URL（含远程路径，如 `https://example.com/remote.php/dav/files/user/Images`）
- **用户名 / 密码** — WebDAV 认证凭据
- 密码以明文存储在 `picdav.yml` 中（该文件已加入 `.gitignore`，不会被提交）

## 公网部署说明

`gallery.py` 可安全暴露到公网：

- **无敏感接口** — 没有配置、删除、上传端点
- **路径式代理** — `/api/proxy/<文件名>` 和 `/api/thumbnail/<文件名>` 不暴露 WebDAV 源站地址
- **URL 安全校验** — 严格校验协议、hostname、端口、路径，防止 URL 绕过
- **无认证信息泄露** — 不暴露任何配置数据
- **API Token 防护** — 所有 API 接口需携带自动生成的 token（Cookie / Header / 查询参数）
- **缩略图端点** — `/api/thumbnail/` 实时生成 JPEG 缩略图并缓存图片尺寸

建议配合 Nginx/Caddy 反向代理加 HTTPS。

## 内置功能

### 🎵 网易云音乐播放器

右下角浮动音乐播放器，基于 [APlayer.js](https://aplayer.js.org/) 构建，开箱即用。

**功能特性：**

- 歌单加载 — 输入网易云歌单 ID 或分享链接即可播放
- 完整控制 — 播放/暂停、上一首/下一首、进度拖拽、音量调节、循环模式
- 播放列表 — 右侧展开显示歌曲列表，支持点击切换
- FAB 自动隐藏 — 鼠标离开时自动右移，悬停或打开面板时展开
- 状态指示 — 小圆点绿/黄/红表示正常/待配置/错误
- 自动持久化 — 歌单 ID、音量设置保存在浏览器 localStorage
- 暗色主题适配 — 随系统/页面主题自动切换

**如何使用：**

1. 点击右下角音符按钮打开播放器面板
2. 点击右上角 ⚙ 按钮打开配置弹窗
3. 输入网易云歌单 **ID**（纯数字）或完整的**分享链接**，例如：
   - `5104642557`
   - `https://music.163.com/playlist?id=5104642557`
4. 点击保存，歌单自动加载

首次打开会自动加载默认歌单（ID: `5104642557`），无需任何配置即可使用。

**VIP 歌曲播放：**

在 `picdav.yml` 的 `music.cookie` 中配置网易云 Cookie（登录 [music.163.com](https://music.163.com) 后从浏览器开发者工具复制），即可播放 VIP 歌曲。**Cookie 必须包含 `MUSIC_U` 字段**，否则无法识别登录状态。留空时仅可播放试听片段。

**技术实现：**

- 后端通过 `music.py`（Flask Blueprint）代理网易云 API，前端不直接请求网易云
- 音频流支持 Range 断点续传，进度拖拽流畅
- 5 分钟进程内缓存，减少重复请求
- 所有 API 端点受 API Token 保护

### 🎨 主题切换

支持三种模式：**日间** / **夜间** / **跟随系统**，切换时使用 View Transitions API 点击扩散动画。

可在 `picdav.yml` 中配置定时切换：

```yaml
theme:
  dark_at: "18:00"   # 每天 18:00 自动切夜间
  light_at: "06:00"  # 每天 06:00 自动切日间
```

留空则禁用定时切换，仅保留手动切换和跟随系统。

### 🔒 API Token

首次运行时自动生成随机 token，持久化到 `config/.api_token`。

三重传递途径：
- **Cookie** — 首次访问页面时自动设置，后续请求浏览器自动携带
- **查询参数** — `?_key=<token>`（兼容旧版）
- **请求头** — `X-API-Token`

### 📊 网站统计

内置 Umami 网站统计脚本，可在模板中配置。

## 项目结构

```
picdav/
├── gallery.py         # 展示页服务，只读图片浏览，适合公网暴露
├── upload.py          # 管理端服务，上传/配置/删除，建议内网使用
├── common.py          # 共享工具库（配置加解密、图片尺寸探测、WebDAV 上传）
├── music.py           # 网易云音乐播放器 Flask Blueprint（歌单/音频代理）
├── protect.py         # API Token 自动生成与校验（Cookie/Header/查询参数）
├── config/
│   ├── image_meta_cache.json    # 图片尺寸缓存
│   └── .api_token               # API Token（首次运行自动生成）
├── templates/
│   ├── upload.html      # 管理端上传/配置页面（upload.py）
│   ├── gallery.html     # 图片库展示页，Masonry 瀑布流布局（gallery.py）
│   └── music_player.html # 音乐播放器浮窗组件（APlayer.js）
├── picdav.yml           # 应用配置（监听地址、端口、WebDAV、音乐、Umami 等）
├── picdav.yml.example   # 应用配置模板
├── pyproject.toml       # 项目元数据与依赖声明
├── requirements.txt     # pip 依赖锁定
├── uv.lock              # uv 依赖锁定
└── README.md            # 项目文档
```

## API 概览

### 展示页 — `gallery.py`

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 首页 |
| `/api/images` | GET | 图片列表（支持 `offset`/`limit` 分页） |
| `/api/proxy/<filename>` | GET | 代理原图（路径式，不暴露源站） |
| `/api/thumbnail/<filename>` | GET | 生成 JPEG 缩略图 |
| `/api/random` | GET | 随机跳转一张图片 |
| `/api/music/playlist?id=` | GET | 获取网易云歌单 |
| `/api/music/song/<id>` | GET | 代理音频流 |

### 管理端 — `upload.py`

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 管理首页 |
| `/api/image/config` | GET/POST | 获取/更新 WebDAV 配置 |
| `/api/image/upload` | POST | 上传图片（支持多文件） |
| `/api/image/list` | GET | 列出已上传图片 |
| `/api/image/delete` | POST | 删除图片 |
| `/api/image/proxy` | GET | 代理图片 |
| `/api/music/playlist?id=` | GET | 获取网易云歌单 |
| `/api/music/song/<id>` | GET | 代理音频流 |
