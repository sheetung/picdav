"""纯展示服务 - 只读图片浏览 (端口 5000，适合公网暴露)

支持图片尺寸缓存 & masonry 布局
"""

from pathlib import Path
import xml.etree.ElementTree as ET
from io import BytesIO
from urllib.parse import urlparse, unquote
import posixpath

import random

import requests
from requests.auth import HTTPBasicAuth
from flask import Flask, request, jsonify, Response, render_template, redirect
from PIL import Image

from common import load_config, enrich_with_dimensions, load_meta_cache, save_meta_cache, make_cache_key

from music import create_music_blueprint
from protect import setup_protection

app = Flask(__name__)
setup_protection(app)
app.register_blueprint(create_music_blueprint())


# ── URL 安全校验 ──

def _normalize_url_path(path):
    """URL 解码后规范化路径，防止 ../../ 穿越"""
    if not path or path == '/':
        return '/'
    return '/' + posixpath.normpath(unquote(path)).lstrip('/')


def _url_is_safe(file_url, allowed_prefix):
    """严格校验 file_url 是否在 allowed_prefix 允许范围内

    绕过方式举例（均已被拦截）:
      startswith: http://a:5005.evil.com/    ← 子域名
      startswith: http://a:5005@evil.com/    ← 凭证混淆
      startswith: http://a:5005/../../etc/   ← 路径穿越
    """
    try:
        f = urlparse(file_url)
        a = urlparse(allowed_prefix)

        # 1) 协议必须一致
        if f.scheme != a.scheme:
            return False
        # 2) hostname 完全匹配（子域名 / @user:pass 都会改 hostname）
        if f.hostname != a.hostname:
            return False
        # 3) 端口一致（urlparse 对默认端口返回 None）
        fp = f.port or (443 if f.scheme == 'https' else 80)
        ap = a.port or (443 if a.scheme == 'https' else 80)
        if fp != ap:
            return False
        # 4) 路径必须在允许前缀下（含 ../../ 防护）
        fp_norm = _normalize_url_path(f.path)
        ap_norm = _normalize_url_path(a.path)
        if not fp_norm.startswith(ap_norm.rstrip('/') + '/'):
            if fp_norm != ap_norm:
                return False
        return True
    except Exception:
        return False


@app.route('/')
def index():
    return render_template('gallery.html')


def _is_safe_filename(filename):
    """校验文件名不含路径穿越字符"""
    return not ('/' in filename or '\\' in filename or '..' in filename)


def _build_webdav_url(config, filename):
    """根据配置和文件名拼出完整的 WebDAV 下载 URL（仅内部使用，不暴露给前端）"""
    server_url = config.get("server_url", "").rstrip("/")
    remote_path = config.get("remote_path", "/Images/").strip("/")
    return f"{server_url}/{remote_path}/{filename}"


def _fetch_images():
    """从WebDAV获取图片列表，返回 [{name, size, width, height, proxy_url, thumb_url}]

    proxy_url / thumb_url 使用纯文件名路径（不暴露内部 WebDAV 地址）
    """
    config = load_config()
    server_url = config.get("server_url", "").rstrip("/")
    username = config.get("username", "")
    password = config.get("password", "")
    remote_path = config.get("remote_path", "/Images/").strip("/")

    if not server_url:
        return None, "未配置"

    dir_url = f"{server_url}/{remote_path}/"
    headers = {"Depth": "1", "Content-Type": "application/xml"}
    body = """<?xml version="1.0" encoding="utf-8"?>
    <D:propfind xmlns:D="DAV:">
        <D:prop>
            <D:getlastmodified/>
            <D:getcontentlength/>
            <D:resourcetype/>
        </D:prop>
    </D:propfind>"""

    resp = requests.request(
        "PROPFIND", dir_url, data=body, headers=headers,
        auth=HTTPBasicAuth(username, password), timeout=30
    )

    if resp.status_code not in (200, 207):
        return None, "获取失败"

    root = ET.fromstring(resp.text)
    ns = {'D': 'DAV:'}
    image_exts = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'}

    raw_images = []
    for elem in root.findall('.//D:response', ns):
        href = elem.find('.//D:href', ns)
        if href is None:
            continue
        filename = href.text.rstrip('/').split('/')[-1]
        if Path(filename).suffix.lower() not in image_exts:
            continue

        content_length = elem.find('.//D:getcontentlength', ns)
        size = int(content_length.text) if content_length is not None else 0
        last_modified = elem.find('.//D:getlastmodified', ns)
        mtime = last_modified.text if last_modified is not None else ""

        # 使用纯文件名路径，不暴露内部 WebDAV 地址
        proxy = f"/api/proxy/{filename}"
        thumb = f"/api/thumbnail/{filename}"
        raw_images.append({
            "name": filename,
            "size": size,
            "modified": mtime,
            "proxy_url": proxy,
            "thumb_url": thumb,
        })

    raw_images.sort(key=lambda x: x["name"], reverse=True)

    # 补上尺寸信息（缓存/探测）
    images = enrich_with_dimensions(raw_images, server_url, remote_path, username, password)
    return images, None


@app.route('/api/images', methods=['GET'])
def list_images():
    """返回图片列表（含宽高），支持分页 offset / limit"""
    images, err = _fetch_images()
    if err:
        return jsonify({"error": err}), 400

    total = len(images)
    offset = request.args.get("offset", 0, type=int)
    limit = request.args.get("limit", 0, type=int)

    if limit > 0:
        images = images[offset:offset + limit]

    return jsonify({"images": images, "total": total})


@app.route('/api/random', methods=['GET'])
def random_image():
    """重定向到随机图片"""
    images, err = _fetch_images()
    if err or not images:
        return jsonify({"error": "暂无图片"}), 404
    img = random.choice(images)
    return redirect(img["proxy_url"])


@app.route('/api/proxy/<filename>')
def proxy_image_path(filename):
    """路径式代理：/api/proxy/20260627-xxx.png → 从配置的 WebDAV 下载（不暴露源站地址）"""
    if not _is_safe_filename(filename):
        return jsonify({"error": "禁止访问"}), 403
    try:
        config = load_config()
        file_url = _build_webdav_url(config, filename)
        return _proxy_response(file_url)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/proxy', methods=['GET'])
def proxy_image():
    """URL 参数式代理：/api/proxy?url=...    保留兼容，前端不再使用"""
    try:
        file_url = request.args.get("url")
        if not file_url:
            return jsonify({"error": "缺少URL"}), 400

        config = load_config()
        server_url = config.get("server_url", "").rstrip("/")
        remote_path = config.get("remote_path", "/Images/").strip("/")

        allowed_prefix = f"{server_url}/{remote_path}/"
        if not server_url or not _url_is_safe(file_url, allowed_prefix):
            return jsonify({"error": "禁止访问"}), 403

        return _proxy_response(file_url)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _proxy_response(file_url):
    """下载并返回图片内容"""
    config = load_config()
    username = config.get("username", "")
    password = config.get("password", "")

    resp = requests.get(
        file_url, auth=HTTPBasicAuth(username, password),
        timeout=30, stream=True
    )
    if resp.status_code != 200:
        return jsonify({"error": "获取失败"}), 400

    return Response(
        resp.content,
        content_type=resp.headers.get('Content-Type', 'image/jpeg'),
        headers={'Cache-Control': 'public, max-age=3600'}
    )


@app.route('/api/thumbnail/<filename>')
def thumbnail_image_path(filename):
    """路径式缩略图：/api/thumbnail/20260627-xxx.png   不暴露源站地址"""
    if not _is_safe_filename(filename):
        return jsonify({"error": "禁止访问"}), 403
    try:
        config = load_config()
        file_url = _build_webdav_url(config, filename)
        return _thumbnail_response(file_url, filename)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/thumbnail', methods=['GET'])
def thumbnail_image():
    """URL 参数式缩略图：/api/thumbnail?url=...  保留兼容，前端不再使用"""
    try:
        file_url = request.args.get("url")
        if not file_url:
            return jsonify({"error": "缺少URL"}), 400

        config = load_config()
        server_url = config.get("server_url", "").rstrip("/")
        remote_path = config.get("remote_path", "/Images/").strip("/")

        if not server_url or not _url_is_safe(file_url, f"{server_url}/{remote_path}/"):
            return jsonify({"error": "禁止访问"}), 403

        filename = file_url.rstrip('/').split('/')[-1]
        return _thumbnail_response(file_url, filename)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _thumbnail_response(file_url, filename):
    """生成缩略图并缓存尺寸信息"""
    config = load_config()
    username = config.get("username", "")
    password = config.get("password", "")

    resp = requests.get(
        file_url, auth=HTTPBasicAuth(username, password),
        timeout=30, stream=True
    )
    if resp.status_code != 200:
        return jsonify({"error": "获取失败"}), 400

    img = Image.open(BytesIO(resp.content))
    orig_w, orig_h = img.size

    # 更新缓存中的尺寸
    cache = load_meta_cache()
    cache[filename] = {
        "_key": cache.get(filename, {}).get("_key", f"{filename}||"),
        "width": orig_w,
        "height": orig_h,
    }
    save_meta_cache(cache)

    buf = BytesIO()
    img.convert('RGB').save(buf, 'JPEG', quality=70)
    buf.seek(0)

    return Response(
        buf.getvalue(),
        content_type='image/jpeg',
        headers={
            'Cache-Control': 'public, max-age=3600',
            'X-Image-Width': str(orig_w),
            'X-Image-Height': str(orig_h),
        }
    )


if __name__ == '__main__':
    app.run(host='192.168.31.238', port=5000, debug=True)
