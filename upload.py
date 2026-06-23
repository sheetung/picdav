"""图片上传与管理服务 - 完整功能 (端口 5001，内部使用)"""

from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET

import requests
from requests.auth import HTTPBasicAuth
from flask import Flask, request, jsonify, Response, render_template

from common import load_config, save_config, generate_filename, upload_to_webdav

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024


@app.route('/')
def index():
    return render_template('index.html')


# ── 配置 ──

@app.route('/api/image/config', methods=['GET'])
def get_config():
    config = load_config()
    if config.get("password"):
        config["password"] = "********"
    return jsonify(config)


@app.route('/api/image/config', methods=['POST'])
def update_config():
    new_config = request.json
    if new_config.get("password") in (None, "", "********"):
        existing = load_config()
        new_config["password"] = existing.get("password", "")
    save_config(new_config)
    return jsonify({"status": "ok"})


# ── 上传 ──

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


# ── 图片列表 ──

@app.route('/api/image/list', methods=['GET'])
def list_files():
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
            "PROPFIND", dir_url, data=body, headers=headers,
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
            filename = href.text.rstrip('/').split('/')[-1]
            if Path(filename).suffix.lower() not in image_exts:
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


# ── 删除 ──

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


# ── 图片代理 ──

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
    app.run(host='127.0.0.1', port=5001, debug=True)
