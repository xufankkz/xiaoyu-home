"""Authenticated release endpoints alongside all existing shared product APIs."""
from pathlib import Path
from http.cookies import SimpleCookie
import hashlib
import hmac
import json
import secrets
import urllib.parse
import urllib.request
from . import VERSION
from .auth import session_valid
from .chat import LocalChat
from .settings import Settings, synchronize_model
from .migration import Migration
from .integrations import Integrations
import base64
from .files import atomic_json, read_json
import release_paths

def install(web):
    runtime = release_paths.runtime()
    workspace = release_paths.workspace()
    settings = Settings(workspace)
    chat = LocalChat(workspace)
    import threading
    # Only replays local persistence operations, never a model request or send.
    def recover_memories():
        import time
        while True:
            chat.flush_memories()
            time.sleep(30)
    threading.Thread(target=recover_memories, name="local-memory-recovery", daemon=True).start()
    migration = Migration(workspace)
    integrations = Integrations(workspace)
    from .services import Services
    services = Services(workspace)
    web._synth = services.speech
    from douyin_social import DouyinSocial
    def public_model(messages):
        result = chat.model.complete(messages, feature='douyin', group='douyin', max_tokens=4096)
        text = result['text'].strip()
        if text.startswith('```'):
            text = '\n'.join(text.splitlines()[1:-1])
        return {'ok': True, 'result': json.loads(text), 'usage': result['usage']}
    web._DOUYIN = DouyinSocial(workspace, snapshot=web.PRODUCT_SETTINGS.snapshot, model=public_model)
    def replace_key(body, group):
        if body.get('revision') != web.PRODUCT_SETTINGS.revision():
            raise ValueError('配置已变化，请重新加载。')
        key = body.get('key', '')
        settings.credentials.set('model:' + group, key)
        return {'saved': True, 'revision': web.PRODUCT_SETTINGS.revision(), 'message': 'Key 已加密保存在本机。'}
    web.PRODUCT_SETTINGS.replace_key = lambda body: replace_key(body, 'chat')
    web.PRODUCT_SETTINGS.replace_drawing_key = lambda body: replace_key(body, 'drawing')
    session_token = secrets.token_urlsafe(32)
    origin = release_paths.url('web')
    base = web.Handler

    class Handler(base):
        def permitted(self, write=False):
            if self.headers.get('Host') != urllib.parse.urlsplit(origin).netloc:
                self._err('请使用本实例的本机地址。', 403)
                return False
            if write and self.headers.get('Origin') not in (None, origin):
                self._err('请从本实例页面提交。', 403)
                return False
            if session_valid(self.headers, session_token):
                return True
            self._err('请通过小鱼之家启动器打开此页面。', 401)
            return False

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path)
            if path.path == '/api/release/health' and hmac.compare_digest(self.headers.get('X-Xiaoyu-Internal', ''), runtime['internal_token']):
                self._ok({'version': VERSION, 'isolated': True})
                return
            if path.path == '/launch':
                if self.headers.get('Host') != urllib.parse.urlsplit(origin).netloc:
                    self._err('请通过本实例启动入口打开。', 403)
                    return
                supplied = urllib.parse.parse_qs(path.query).get('ticket', [''])[0]
                if not hmac.compare_digest(supplied, runtime['launch_token']):
                    self._err('此启动链接已失效，请重新从软件打开。', 401)
                    return
                self.send_response(303)
                self.send_header('Set-Cookie', 'xiaoyu-session=' + session_token + '; HttpOnly; SameSite=Strict; Path=/')
                self.send_header('Location', '/')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            if not self.permitted():
                return
            try:
                if path.path == '/api/release/state':
                    self._ok({'settings': settings.read(), 'usage': chat.model.usage(), 'version': VERSION})
                    return
                if path.path == '/api/release/chat':
                    self._ok({'items': chat.history()})
                    return
                if path.path == '/api/release/integrations':
                    self._ok({'settings': settings.read(), **integrations.state()})
                    return
                if path.path == '/api/asset':
                    filename = urllib.parse.parse_qs(path.query).get('name', [''])[0]
                    if not (Path(web.ASSET_DIR) / Path(filename).name).is_file():
                        self._send(200, (workspace / 'xiaoyu-web/static/release-fish.svg').read_bytes(), 'image/svg+xml')
                        return
                return super().do_GET()
            except ValueError as exc:
                self._err(str(exc), 400)

        def do_POST(self):
            if not self.permitted(write=True):
                return
            path = urllib.parse.urlsplit(self.path).path
            if not path.startswith('/api/release/'):
                return super().do_POST()
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 180 * 1024 * 1024:
                    raise ValueError('请求大小无效')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('请求必须为对象')
                if path == '/api/release/settings':
                    result = settings.save(body)
                    synchronize_model(workspace, release_paths.runtime())
                    self._ok(result)
                elif path == '/api/release/chat':
                    self._ok(chat.send(body))
                elif path == '/api/release/integrations':
                    result = integrations.save(body)
                    web.TTS_BASE = release_paths.url('tts')
                    web.SNOWLUMA_BASE = release_paths.url('onebot')
                    with web._CACHE_LOCK:
                        web._CACHE.clear()
                    self._ok({'settings': settings.read(), **result})
                elif path == '/api/release/integrations/check':
                    self._ok(integrations.check())
                elif path == '/api/release/memes/import':
                    from .memes import import_image
                    result = import_image(workspace, body)
                    try:
                        request = urllib.request.Request(release_paths.url('memes', '/meme/refresh'), data=b'{}',
                            headers={'Content-Type': 'application/json', 'X-Xiaoyu-Internal': runtime['internal_token']})
                        with urllib.request.urlopen(request, timeout=3) as response:
                            response.read()
                    except OSError:
                        result['refresh_pending'] = True
                    self._ok(result)
                elif path == '/api/release/migration/plan':
                    result = migration.inspect(base64.b64decode(body['file'], validate=True), body.get('kind', 'fish'), body.get('password', ''))
                    self._ok(result)
                elif path == '/api/release/migration/apply':
                    self._ok(migration.apply(body['plan_id']))
                elif path == '/api/release/migration/export':
                    raw = migration.export(body['password'])
                    self._ok({'filename': '我的鱼-' + VERSION + '.fish', 'file': base64.b64encode(raw).decode(), 'encrypted': True, 'credentials_included': False})
                else:
                    self._err('未知发行版接口', 404)
            except (ValueError, KeyError, TypeError) as exc:
                self._err(str(exc) if isinstance(exc, ValueError) else '提交的设置不完整或格式无效', 400)
            except Exception:
                self._err('本地操作未能完成，请检查当前实例日志。', 500)
    web.Handler = Handler
