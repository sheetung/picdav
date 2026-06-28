"""API protection — 自动生成随机 token，无需任何配置

通过 Cookie + 查询参数 + 请求头三重途径传递 token：
  - Cookie: 首次访问页面时自动设置，后续请求浏览器自动携带
  - 查询参数: ?_key=<token>（兼容旧版）
  - 请求头: X-API-Token

首次运行时 token 自动生成并持久化到 config/.api_token。
"""

import secrets
from pathlib import Path
from functools import wraps
from flask import request, jsonify

_TOKEN_FILE = Path(__file__).parent / "config" / ".api_token"
_token_cache = None


def get_token():
    """返回 API token（首次自动生成并持久化）"""
    global _token_cache
    if _token_cache is not None:
        return _token_cache

    if _TOKEN_FILE.exists():
        _token_cache = _TOKEN_FILE.read_text().strip()
        return _token_cache

    _token_cache = secrets.token_urlsafe(24)
    _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_FILE.write_text(_token_cache)
    return _token_cache


def require_token(f):
    """Decorator：校验请求中携带的 API token

    依次检查：Cookie → 查询参数 ?_key= → 请求头 X-API-Token
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token = get_token()
        req_token = (
            request.cookies.get('api_token', '')
            or request.args.get('_key', '')
            or request.headers.get('X-API-Token', '')
        )
        if not secrets.compare_digest(token, req_token):
            return jsonify({"error": "未授权的请求"}), 403
        return f(*args, **kwargs)
    return decorated


def api_token_context():
    """Flask context processor：向所有模板注入 api_token 变量"""
    return {'api_token': get_token()}


def setup_protection(app):
    """快捷注册：context_processor + after_request Cookie 注入"""
    app.context_processor(api_token_context)

    @app.after_request
    def _set_token_cookie(response):
        """每个响应都设置 api_token Cookie，确保前端 JS/CSS/音频请求自动携带"""
        response.set_cookie(
            'api_token', get_token(),
            httponly=False,      # JS 也可读取
            samesite='Lax',
            max_age=86400,       # 24 小时
            path='/'
        )
        return response

    return app
