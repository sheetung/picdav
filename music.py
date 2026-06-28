"""NetEase Cloud Music API proxy - reusable Flask Blueprint

为"图片上传工具"提供音乐播放器后端支持。

使用方式:
    from music import create_music_blueprint
    app.register_blueprint(create_music_blueprint())

提供两个端点:
  GET /api/music/playlist?id=<歌单ID>    — 获取歌单及歌曲列表
  GET /api/music/song/<歌曲ID>           — 代理音频流（支持 Range 断点续传 / 跳转）
"""

import time
import requests
from flask import Blueprint, jsonify, Response, request
from protect import require_token


# ── 简单的进程内缓存，避免重复请求网易云 API ──
_playlist_cache = {}
_CACHE_TTL = 300  # 5 分钟


def _netease_headers():
    """网易云 API 通用请求头"""
    return {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        ),
        'Referer': 'https://music.163.com/',
    }


def create_music_blueprint():
    """创建并返回一个 Flask Blueprint，包含音乐相关的路由。"""
    bp = Blueprint('music', __name__)

    # ──────────────────────────────────────────────
    #  获取歌单详情 & 歌曲列表
    # ──────────────────────────────────────────────

    @bp.route('/api/music/playlist')
    @require_token
    def get_playlist():
        playlist_id = request.args.get('id', '').strip()

        if not playlist_id or not playlist_id.isdigit():
            return jsonify({"error": "请提供有效的歌单 ID"}), 400

        # 命中缓存？
        cache_key = f"playlist_{playlist_id}"
        now = time.time()
        cached = _playlist_cache.get(cache_key)
        if cached and (now - cached['time']) < _CACHE_TTL:
            return jsonify(cached['data'])

        # 请求网易云 API
        try:
            resp = requests.get(
                f'https://music.163.com/api/v3/playlist/detail?id={playlist_id}',
                headers=_netease_headers(), timeout=15
            )
        except requests.RequestException as e:
            return jsonify({"error": f"请求网易云失败: {e}"}), 502

        if resp.status_code != 200:
            return jsonify({"error": f"网易云 API 返回 {resp.status_code}"}), 502

        try:
            data = resp.json()
        except Exception as e:
            return jsonify({"error": f"解析响应失败: {e}"}), 502

        playlist = data.get('playlist')
        if not playlist:
            return jsonify({"error": "歌单不存在或已删除"}), 404

        songs = []
        for s in playlist.get('tracks', []):
            # API v3 中缩写字段: ar → artists, al → album, dt → duration (ms)
            artists = s.get('ar', s.get('artists', []))
            album = s.get('al', s.get('album', {}))
            duration_ms = s.get('dt', s.get('duration', 0))
            songs.append({
                "id": s['id'],
                "name": s['name'],
                "artist": ' / '.join(a['name'] for a in artists),
                "cover": album.get('picUrl', ''),
                "duration": duration_ms // 1000,  # ms → s
            })

        result = {
            "id": playlist.get('id'),
            "name": playlist.get('name'),
            "cover": playlist.get('coverImgUrl', ''),
            "songCount": len(songs),
            "songs": songs,
        }

        # 写入缓存
        _playlist_cache[cache_key] = {'time': now, 'data': result}

        return jsonify(result)

    # ──────────────────────────────────────────────
    #  代理音频流（支持 Range 请求/跳转播放）
    # ──────────────────────────────────────────────

    @bp.route('/api/music/song/<int:song_id>')
    @require_token
    def get_song(song_id):
        """通过网易云 outer URL 获取 CDN 地址，再代理音频流。"""

        h = _netease_headers()

        # Step 1: 获取 CDN 真实地址 (302 重定向)
        try:
            head_resp = requests.get(
                f'https://music.163.com/song/media/outer/url?id={song_id}.mp3',
                headers=h, timeout=15, allow_redirects=False
            )
        except requests.RequestException as e:
            return jsonify({"error": str(e)}), 502

        cdn_url = None
        if head_resp.status_code in (301, 302, 303, 307, 308):
            cdn_url = head_resp.headers.get('Location')

        if not cdn_url:
            return jsonify({"error": "无法获取音频 CDN 地址"}), 502

        # Step 2: 把客户端的 Range header 透传给 CDN
        range_h = request.headers.get('Range')
        if range_h:
            h['Range'] = range_h

        try:
            cdn_resp = requests.get(
                cdn_url, headers=h, timeout=60, stream=True
            )
        except requests.RequestException as e:
            return jsonify({"error": str(e)}), 502

        if cdn_resp.status_code not in (200, 206):
            return jsonify({"error": f"CDN 返回 {cdn_resp.status_code}"}), 502

        # 透传 CDN 响应头 (Content-Type 单独处理，避免 charset 干扰)
        passthrough = {}
        for key in ('Content-Range', 'Content-Length'):
            if key in cdn_resp.headers:
                passthrough[key] = cdn_resp.headers[key]

        # 清理 Content-Type（CDN 有时会带 charset 后缀）
        ct = cdn_resp.headers.get('Content-Type', 'audio/mpeg').split(';')[0].strip()
        if not ct.startswith('audio/'):
            ct = 'audio/mpeg'

        return Response(
            cdn_resp.iter_content(chunk_size=32768),
            content_type=ct,
            status=cdn_resp.status_code,
            headers={
                **passthrough,
                'Cache-Control': 'public, max-age=3600',
                'Accept-Ranges': 'bytes',
            }
        )

    return bp
