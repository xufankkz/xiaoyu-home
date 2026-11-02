"""Local Mandarin transcription and sampled video inspection; no QQ send API."""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import release_paths
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
def executable(name):
    from home_runtime.files import read_json
    folder = read_json(ROOT / 'release/settings.json').get('integrations', {}).get('media', {}).get('executable_dir', '')
    target = Path(folder) / (name + '.exe') if folder else ROOT.parent / 'components/media/ffmpeg/bin' / (name + '.exe')
    if not target.is_file():
        raise ValueError('尚未安装媒体解码工具，请在“接入与组件”填写自己的 FFmpeg bin 文件夹。')
    return target
MAX_BYTES = 64 * 1024 * 1024
MAX_SECONDS = 300
_gate = threading.Lock()
_error = ''


def run(args, timeout=35):
    result = subprocess.run([str(x) for x in args], capture_output=True, timeout=timeout,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('媒体解码失败：' + result.stderr.decode('utf-8', 'replace')[:700])
    return result.stdout


def probe(path):
    return json.loads(run([executable('ffprobe'), '-v', 'error', '-show_format', '-show_streams', '-of', 'json', path]))


def transcribe(path, duration):
    import numpy as np
    audio = run([executable('ffmpeg'), '-nostdin', '-v', 'error', '-i', path, '-vn', '-ac', '1', '-ar', '16000',
                 '-t', str(MAX_SECONDS), '-f', 'f32le', 'pipe:1'], timeout=50)
    samples = np.frombuffer(audio, dtype='<f4')
    # Silence is reported as silence, never fed to ASR as a supposed utterance.
    if not len(samples) or float(np.sqrt(np.mean(samples ** 2))) < 0.002:
        return {'text': '', 'segments': [], 'silence': True}
    from home_runtime.services import Services
    import wave
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
        wav.writeframes((np.clip(samples, -1, 1) * 32767).astype('<i2').tobytes())
    return Services(ROOT).transcribe(buffer.getvalue())



def video_frames(path, duration, folder):
    from PIL import Image, ImageDraw
    count = min(8, max(1, int(duration // 3) + 1))
    times = [round(max(0, duration - 0.5) * i / max(1, count - 1), 2) for i in range(count)]
    columns = min(4, count)
    sheet = Image.new('RGB', (columns * 320, ((count + columns - 1) // columns) * 210), '#152433')
    draw = ImageDraw.Draw(sheet)
    for i, timestamp in enumerate(times):
        target = folder / f'frame-{i}.png'
        run([executable('ffmpeg'), '-nostdin', '-v', 'error', '-threads', '1', '-ss', str(timestamp), '-i', path, '-frames:v', '1',
             '-vf', 'scale=320:180:force_original_aspect_ratio=decrease', '-filter_threads', '1', '-threads', '1', '-y', target])
        with Image.open(target) as frame:
            frame = frame.convert('RGB'); x = (i % columns) * 320; y = (i // columns) * 210
            sheet.paste(frame, (x + (320 - frame.width) // 2, y + 22 + (180 - frame.height) // 2))
            draw.text((x + 8, y + 5), f'{timestamp:.2f}s', fill='white')
    data = io.BytesIO(); sheet.save(data, 'JPEG', quality=87)
    return {'data': base64.b64encode(data.getvalue()).decode('ascii'), 'mimeType': 'image/jpeg',
            'timestamps': times, 'sampled': True}


def analyze(raw, kind, *, audio_enabled=True):
    if kind not in ('record', 'video'):
        raise ValueError('只接收语音或视频')
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError('媒体为空或超过 64 MB')
    with tempfile.TemporaryDirectory(prefix='xiaoyu-media-') as temp:
        folder = Path(temp); source = folder / 'input.media'; source.write_bytes(raw)
        info = probe(source)
        duration = float(info.get('format', {}).get('duration') or 0)
        if not 0 < duration <= MAX_SECONDS:
            raise ValueError('目前支持 5 分钟以内的语音／视频；未分析超长内容，请分段发送')
        streams = info.get('streams') or []
        has_audio = any(s.get('codec_type') == 'audio' for s in streams)
        has_video = any(s.get('codec_type') == 'video' for s in streams)
        if kind == 'video' and not has_video:
            raise ValueError('未找到可解码的视频画面')
        out = {'ok': True, 'kind': kind, 'duration': round(duration, 2), 'language': 'zh',
               'sha256': hashlib.sha256(raw).hexdigest(), 'has_audio': has_audio, 'has_video': has_video}
        if has_video and kind == 'video':
            out['frames'] = video_frames(source, duration, folder)
        if has_audio and audio_enabled:
            try:
                out['transcript'] = transcribe(source, duration)
            except Exception as exc:
                if kind == 'record':
                    raise
                out['audio_error'] = '视频声音未能转写：' + str(exc)[:200]
        elif kind == 'record':
            raise ValueError('未找到可解码的音轨')
        return out


def token():
    try:
        cfg = json.loads((ROOT / 'qq-bridge/config.json').read_text(encoding='utf-8-sig'))
        return str(cfg.get('consoleToken') or (ROOT / 'qq-bridge/state/console-token').read_text().strip())
    except Exception:
        return ''


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, body):
        raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status); self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw))); self.send_header('Cache-Control', 'no-store')
        self.end_headers(); self.wfile.write(raw)

    def do_GET(self):
        if self.path != '/health':
            return self.reply(404, {'ok': False})
        from home_runtime.files import read_json
        config = read_json(ROOT / 'release/settings.json').get('integrations', {}).get('asr', {})
        try:
            decoder = executable('ffmpeg').is_file() and executable('ffprobe').is_file()
        except ValueError:
            decoder = False
        self.reply(200, {'ok': True, 'asr': 'configured-openai-compatible',
                         'configured': bool(config.get('enabled') and config.get('http_url')),
                         'decoder_present': decoder, 'max_seconds': MAX_SECONDS,
                         'max_bytes': MAX_BYTES, 'busy': _gate.locked(), 'last_error': _error})


    def do_POST(self):
        global _error
        self.connection.settimeout(30)
        if self.path != '/analyze':
            return self.reply(404, {'ok': False})
        configured = token(); supplied = self.headers.get('x-console-token', '')
        if not configured or not hmac.compare_digest(configured, supplied) or self.headers.get('Origin'):
            return self.reply(403, {'ok': False, 'error': '只接受本机服务调用'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= MAX_BYTES * 4 // 3 + 4096:
                raise ValueError('媒体请求大小超限')
            body = json.loads(self.rfile.read(size))
            raw = base64.b64decode(body.get('data', ''), validate=True)
        except Exception as exc:
            return self.reply(400, {'ok': False, 'error': str(exc)[:300]})
        if not _gate.acquire(blocking=False):
            return self.reply(429, {'ok': False, 'error': '正在理解上一条媒体，请稍后再试'})
        try:
            began = time.monotonic(); out = analyze(raw, body.get('kind'))
            out['elapsed_seconds'] = round(time.monotonic() - began, 2); _error = ''
            self.reply(200, out)
        except Exception as exc:
            _error = str(exc)[:400]; self.reply(422, {'ok': False, 'error': _error})
        finally:
            _gate.release()

    def log_message(self, fmt, *args):
        print('[media] ' + fmt % args, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--port', type=int, default=18893)
    args = parser.parse_args()
    os.environ.setdefault('HF_HUB_OFFLINE', '1')
    os.environ.setdefault('MODELSCOPE_OFFLINE', '1')
    print(f'Local media service listening on 127.0.0.1:{args.port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
