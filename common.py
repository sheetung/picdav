"""共享工具函数 - 图片上传工具"""

import json
import hashlib
from datetime import datetime
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth
from cryptography.fernet import Fernet

CONFIG_FILE = Path(__file__).parent / "config" / "image_uploader.json"
KEY_FILE = Path(__file__).parent / "config" / ".encryption_key"


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
