"""纯展示服务 - 只读图片浏览 (端口 5000，适合公网暴露)"""

from pathlib import Path
import xml.etree.ElementTree as ET

import requests
from requests.auth import HTTPBasicAuth
from flask import Flask, request, jsonify, Response, render_template

from common import load_config

app = Flask(__name__)


@app.route('/')
def index():
    return render_template('gallery.html')


@app.route('/api/images', methods=['GET'])
def list_images():
    """返回图片列表，只包含文件名和代理URL，不暴露WebDAV真实路径"""
    try:
        config = load_config()
        server_url = config.get("server_url", "").rstrip("/")
        username = config.get("username", "")
        password = config.get("password", "")
        remote_path = config.get("remote_path", "/Images/").strip("/")

        if not server_url:
            return jsonify({"error": "未配置"}), 400

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

        response = requests.request(
            "PROPFIND", dir_url, data=body, headers=headers,
            auth=HTTPBasicAuth(username, password), timeout=30
        )

        if response.status_code not in (200, 207):
            return jsonify({"error": f"获取失败"}), 400

        root = ET.fromstring(response.text)
        ns = {'D': 'DAV:'}
        image_exts = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'}

        images = []
        for resp_elem in root.findall('.//D:response', ns):
            href = resp_elem.find('.//D:href', ns)
            if href is None:
                continue
            filename = href.text.rstrip('/').split('/')[-1]
            if Path(filename).suffix.lower() not in image_exts:
                continue

            raw_url = f"{server_url}/{remote_path}/{filename}"
            images.append({
                "name": filename,
                "proxy_url": f"/api/proxy?url={raw_url}",
                "thumb_url": f"/api/proxy?url={raw_url}",
            })

        images.sort(key=lambda x: x["name"], reverse=True)
        return jsonify({"images": images})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
    app.run(host='127.0.0.1', port=5000, debug=True)
