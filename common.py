"""共享工具函数 - 图片上传工具"""

import json
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from io import BytesIO
from pathlib import Path

import yaml
import requests
from requests.auth import HTTPBasicAuth
from PIL import Image

META_CACHE_FILE = Path(__file__).parent / "config" / "image_meta_cache.json"
PICDAV_CFG = Path(__file__).parent / "picdav.yml"


def load_app_config():
    """加载 picdav.yml 应用配置（音乐默认歌单、Umami 统计等）"""
    if not PICDAV_CFG.exists():
        return {}
    with open(PICDAV_CFG, encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def register_app_config(app):
    """向 Flask 模板上下文注入 app_config 变量（音乐、Umami 等配置）"""
    cfg = load_app_config()
    # 过滤掉敏感配置，不注入模板上下文
    safe_cfg = {k: v for k, v in cfg.items() if k not in ("webdav",)}

    @app.context_processor
    def inject_app_config():
        return {'app_config': safe_cfg}

    return app


def get_server_config(service="app"):
    """从 picdav.yml 读取指定服务的监听配置，返回 (host, port)"""
    cfg = load_app_config()
    svc = cfg.get("server", {}).get(service, {})
    if not svc:
        svc = cfg.get("server", {})  # 兼容扁平结构
    host = svc.get("host", "127.0.0.1")
    port = int(svc.get("port", 5000))
    return host, port


def get_music_cookie():
    """从 picdav.yml 读取网易云音乐 Cookie（用于 VIP 歌曲播放）"""
    cfg = load_app_config()
    cookie = cfg.get("music", {}).get("cookie", "")
    return cookie or ""


def load_config():
    """从 picdav.yml 读取 WebDAV 配置"""
    cfg = load_app_config()
    return cfg.get("webdav", {})


def save_config(config):
    """保存 WebDAV 配置到 picdav.yml（文本替换，保留注释）"""
    lines = PICDAV_CFG.read_text(encoding="utf-8").splitlines(keepends=True)

    def _webdav_block(prefix=""):
        return (
            f"{prefix}webdav:\n"
            f'{prefix}  # WebDAV 服务器配置（完整 URL，含远程路径）\n'
            f'{prefix}  server_url: "{config.get("server_url", "")}"\n'
            f'{prefix}  username: "{config.get("username", "")}"\n'
            f'{prefix}  password: "{config.get("password", "")}"\n'
            f"{prefix}\n"
        )

    # 查找 webdav: 所在行号
    start = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith("webdav:"):
            start = i
            break
    else:
        # 不存在则追加到 server 块之后
        for i, line in enumerate(lines):
            if line.lstrip().startswith("music:"):
                lines.insert(i, _webdav_block())
                break
        else:
            lines.append(_webdav_block())
        PICDAV_CFG.write_text("".join(lines), encoding="utf-8")
        return

    # 确定结尾（下一个同层 key 或文件尾）
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = start + 1
    while end < len(lines):
        if lines[end].strip() == "" or lines[end].lstrip().startswith("#"):
            end += 1
            continue
        if lines[end] and len(lines[end]) - len(lines[end].lstrip()) <= indent:
            break
        end += 1

    lines[start:end] = [_webdav_block(lines[start][:indent])]
    PICDAV_CFG.write_text("".join(lines), encoding="utf-8")


# ── 图片尺寸缓存 ──


def load_meta_cache():
    if META_CACHE_FILE.exists():
        try:
            return json.loads(META_CACHE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_meta_cache(cache):
    META_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    META_CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def make_cache_key(name, size, modified):
    return f"{name}|{size}|{modified}"


def probe_dimensions(url, username, password, max_bytes=262144):
    """流式下载图片前 max_bytes 字节，通过 PIL 读取宽高

    大部分图片格式的尺寸信息在文件头几 KB 内:
      - PNG:  byte 16-24 (IHDR)
      - GIF:  byte 6-9
      - WebP: byte 24-29
      - BMP:  byte 18-25
      - JPEG: 前 2-4 KB (SOF0 marker)
    JPEG 带内嵌缩略图时可能更靠后，max_bytes=256K 足够覆盖 >99% 的场景。
    """
    try:
        resp = requests.get(
            url, auth=HTTPBasicAuth(username, password),
            stream=True, timeout=15
        )
        if resp.status_code != 200:
            return None, None

        chunk = bytearray()
        for data in resp.iter_content(chunk_size=65536):
            chunk.extend(data)
            if len(chunk) >= max_bytes:
                break
        resp.close()

        if not chunk:
            return None, None

        img = Image.open(BytesIO(bytes(chunk)))
        return img.size  # (width, height)
    except Exception:
        return None, None


def enrich_with_dimensions(images, server_url, username, password):
    """为图片列表补上 width / height，优先命中本地缓存，未命中则并发探测

    images: [{name, size, modified, ...}]  —— 会原地修改加入 width/height
    返回 images（方便链式调用）
    """
    cache = load_meta_cache()

    need_probe = []
    for img in images:
        key = make_cache_key(img["name"], img.get("size", 0), img.get("modified", ""))
        entry = cache.get(img["name"], {})
        if entry.get("_key") == key and entry.get("width") is not None and entry.get("height") is not None:
            img["width"] = entry["width"]
            img["height"] = entry["height"]
        else:
            img["width"] = None
            img["height"] = None
            need_probe.append(img)

    if need_probe:
        max_workers = min(10, len(need_probe))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            fut_map = {}
            for img in need_probe:
                file_url = f"{server_url.rstrip('/')}/{img['name']}"
                fut = pool.submit(probe_dimensions, file_url, username, password)
                fut_map[fut] = img

            for fut in as_completed(fut_map):
                img = fut_map[fut]
                try:
                    w, h = fut.result()
                except Exception:
                    w, h = None, None
                img["width"] = w
                img["height"] = h
                cache[img["name"]] = {
                    "_key": make_cache_key(img["name"], img.get("size", 0), img.get("modified", "")),
                    "width": w,
                    "height": h,
                }

        save_meta_cache(cache)

    # 仍为 None 的用 4:3 兜底
    for img in images:
        if img.get("width") is None or img.get("height") is None:
            img["width"], img["height"] = 4, 3

    return images


# ── 文件名生成 ──


def generate_filename(image_data):
    date_str = datetime.now().strftime("%Y%m%d")
    content_hash = hashlib.md5(image_data).hexdigest()[:12]
    return f"{date_str}-{content_hash}.png"


def upload_to_webdav(image_data, filename, config):
    server_url = config.get("server_url", "").rstrip("/")
    username = config.get("username", "")
    password = config.get("password", "")

    if not server_url:
        raise ValueError("请先配置WebDAV服务器地址")

    upload_url = f"{server_url}/{filename}"

    dir_url = f"{server_url}/"
    try:
        requests.request("MKCOL", dir_url, auth=HTTPBasicAuth(username, password), timeout=10)
    except Exception:
        pass

    response = requests.put(
        upload_url, data=image_data,
        auth=HTTPBasicAuth(username, password),
        headers={"Content-Type": "image/png"}, timeout=30
    )

    if response.status_code not in (200, 201, 204):
        raise Exception(f"上传失败: HTTP {response.status_code}")

    return upload_url
