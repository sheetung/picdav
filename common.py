"""共享工具函数 - 图片上传工具"""

import json
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from io import BytesIO
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth
from cryptography.fernet import Fernet
from PIL import Image

CONFIG_FILE = Path(__file__).parent / "config" / "image_uploader.json"
KEY_FILE = Path(__file__).parent / "config" / ".encryption_key"
META_CACHE_FILE = Path(__file__).parent / "config" / "image_meta_cache.json"


def _get_cipher():
    if KEY_FILE.exists():
        key = KEY_FILE.read_bytes()
    else:
        key = Fernet.generate_key()
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_bytes(key)
    return Fernet(key)


def encrypt_password(password):
    if not password:
        return ""
    return _get_cipher().encrypt(password.encode()).decode()


def decrypt_password(encrypted):
    if not encrypted:
        return ""
    try:
        return _get_cipher().decrypt(encrypted.encode()).decode()
    except Exception:
        return None


def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        password = config.get("password", "")
        if password:
            decrypted = decrypt_password(password)
            if decrypted is not None:
                config["password"] = decrypted
        return config
    return {}


def save_config(config):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    password = config.get("password", "")
    if password:
        config["password"] = encrypt_password(password)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


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


def enrich_with_dimensions(images, server_url, remote_path, username, password):
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
                file_url = f"{server_url}/{remote_path.strip('/')}/{img['name']}"
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
    remote_path = config.get("remote_path", "/Images/").strip("/")

    if not server_url:
        raise ValueError("请先配置WebDAV服务器地址")

    upload_url = f"{server_url}/{remote_path}/{filename}"

    dir_url = f"{server_url}/{remote_path}/"
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
