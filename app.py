#!/usr/bin/env python3
"""图片上传工具 - 独立版"""

import json
import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from flask import Flask, request, jsonify, Response, render_template, send_from_directory
from PIL import Image
import requests
from requests.auth import HTTPBasicAuth
import io

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

CONFIG_FILE = Path(__file__).parent / "config" / "image_uploader.json"


def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(config):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
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


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/image/config', methods=['GET'])
def get_config():
    return jsonify(load_config())


@app.route('/api/image/config', methods=['POST'])
def update_config():
    config = request.json
    save_config(config)
    return jsonify({"status": "ok"})


@app.route('/api/image/upload', methods=['POST'])
def upload():
    try:
        files = request.files.getlist('files')
        if not files or all(f.filename == '' for f in files):
            return jsonify({"error": "没有文件"}), 400

        config = load_config()
        if not config.get("server_url"):
            return jsonify({"error": "请先配置WebDAV"}), 400

        results = []
        for file in files:
            if file.filename == '':
                continue
            try:
                image_data = file.read()
                filename = generate_filename(image_data)
                url = upload_to_webdav(image_data, filename, config)
                results.append({
                    "status": "成功",
                    "filename": filename,
                    "url": url,
                    "size": len(image_data),
                    "time": datetime.now().strftime("%H:%M:%S")
                })
            except Exception as e:
                results.append({
                    "status": "失败",
                    "filename": file.filename,
                    "error": str(e),
                    "time": datetime.now().strftime("%H:%M:%S")
                })

        return jsonify({"results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/image/list', methods=['GET'])
def list_files():
    """列出WebDAV目录下的图片文件"""
    try:
        config = load_config()
        server_url = config.get("server_url", "").rstrip("/")
        username = config.get("username", "")
        password = config.get("password", "")
        remote_path = config.get("remote_path", "/Images/").strip("/")

        if not server_url:
            return jsonify({"error": "请先配置WebDAV"}), 400

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
            "PROPFIND", dir_url,
            data=body, headers=headers,
            auth=HTTPBasicAuth(username, password), timeout=30
        )

        if response.status_code not in (200, 207):
            return jsonify({"error": f"获取目录失败: HTTP {response.status_code}"}), 400

        root = ET.fromstring(response.text)
        ns = {'D': 'DAV:'}

        files = []
        image_exts = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'}

        for response_elem in root.findall('.//D:response', ns):
            href = response_elem.find('.//D:href', ns)
            if href is None:
                continue
            href_text = href.text
            filename = href_text.rstrip('/').split('/')[-1]
            ext = Path(filename).suffix.lower()
            if ext not in image_exts:
                continue

            content_length = response_elem.find('.//D:getcontentlength', ns)
            size = int(content_length.text) if content_length is not None else 0

            last_modified = response_elem.find('.//D:getlastmodified', ns)
            mtime = last_modified.text if last_modified is not None else ""

            file_url = f"{server_url}/{remote_path}/{filename}"
            files.append({"name": filename, "url": file_url, "size": size, "modified": mtime})

        files.sort(key=lambda x: x.get("modified", ""), reverse=True)
        return jsonify({"files": files})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/image/delete', methods=['POST'])
def delete_file():
    try:
        data = request.json
        file_url = data.get("url")
        if not file_url:
            return jsonify({"error": "缺少文件URL"}), 400

        config = load_config()
        username = config.get("username", "")
        password = config.get("password", "")

        response = requests.delete(file_url, auth=HTTPBasicAuth(username, password), timeout=30)
        if response.status_code in (200, 204, 404):
            return jsonify({"status": "ok"})
        else:
            return jsonify({"error": f"删除失败: HTTP {response.status_code}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/image/proxy', methods=['GET'])
def proxy_image():
    try:
        file_url = request.args.get("url")
        if not file_url:
            return jsonify({"error": "缺少URL参数"}), 400

        config = load_config()
        username = config.get("username", "")
        password = config.get("password", "")

        response = requests.get(file_url, auth=HTTPBasicAuth(username, password), timeout=30, stream=True)
        if response.status_code != 200:
            return jsonify({"error": "获取图片失败"}), 400

        return Response(
            response.content,
            content_type=response.headers.get('Content-Type', 'image/jpeg'),
            headers={'Cache-Control': 'public, max-age=3600'}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)
