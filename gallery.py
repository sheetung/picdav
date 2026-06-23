"""纯展示服务 - 只读图片浏览 (端口 5000，适合公网暴露)"""

from pathlib import Path
import xml.etree.ElementTree as ET

import random

import requests
from requests.auth import HTTPBasicAuth
from flask import Flask, request, jsonify, Response, render_template, redirect

from common import load_config

app = Flask(__name__)


@app.route('/')
def index():
    return render_template('gallery.html')


def _fetch_images():
    """从WebDAV获取图片列表，返回 [{name, proxy_url, raw_url}]"""
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

    images = []
    for elem in root.findall('.//D:response', ns):
        href = elem.find('.//D:href', ns)
        if href is None:
            continue
        filename = href.text.rstrip('/').split('/')[-1]
        if Path(filename).suffix.lower() not in image_exts:
            continue
        proxy = f"/api/proxy?url={server_url}/{remote_path}/{filename}"
        images.append({
            "name": filename,
            "proxy_url": proxy,
            "thumb_url": proxy,
        })

    images.sort(key=lambda x: x["name"], reverse=True)
    return images, None


@app.route('/api/images', methods=['GET'])
def list_images():
    """返回图片列表，支持分页参数 offset / limit"""
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


@app.route('/api/proxy', methods=['GET'])
def proxy_image():
    """代理图片，只允许请求已配置的WebDAV服务器上的图片"""
    try:
        file_url = request.args.get("url")
        if not file_url:
            return jsonify({"error": "缺少URL"}), 400

        config = load_config()
        server_url = config.get("server_url", "").rstrip("/")

        # 安全校验：只代理配置的WebDAV服务器上的图片
        if not server_url or not file_url.startswith(server_url):
            return jsonify({"error": "禁止访问"}), 403

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
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(host='192.168.31.238', port=5000, debug=True)
