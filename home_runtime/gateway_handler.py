"""Metered OpenAI endpoint with tool-call preservation and SSE framing."""
from http.server import BaseHTTPRequestHandler
import hmac
import json

def gateway_handler(client, token):
    from .services import Services
    services = Services(client.workspace)
    class Handler(BaseHTTPRequestHandler):
        def permitted(self):
            supplied = self.headers.get('X-Xiaoyu-Internal', '')
            bearer = self.headers.get('Authorization', '').removeprefix('Bearer ')
            return hmac.compare_digest(supplied, token) or hmac.compare_digest(bearer, token)

        def respond(self, status, payload):
            raw = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if not self.permitted():
                return self.respond(403, {'error': {'message': 'Local authentication required'}})
            models = client.settings().get('models', {})
            self.respond(200, {'object': 'list', 'data': [{'id': model['model'], 'object': 'model'} for model in models.values()]})

        def do_POST(self):
            if not self.permitted():
                return self.respond(403, {'error': {'message': 'Local authentication required'}})
            try:
                groups = {'/v1/chat/completions': 'chat', '/v1/douyin/chat/completions': 'douyin', '/v1/vision/chat/completions': 'vision'}
                if self.path not in groups and self.path not in ('/v1/images/generations', '/v1/audio/speech'):
                    raise ValueError('不支持的模型接口')
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length < 8 * 1024 * 1024:
                    raise ValueError('请求大小无效')
                body = json.loads(self.rfile.read(length))
                if self.path == '/v1/images/generations':
                    return self.respond(200, services.image(body))
                if self.path == '/v1/audio/speech':
                    _, raw, _ = services.speech(body.get('input') or body.get('text'), body.get('tone', 'gentle'))
                    self.send_response(200)
                    self.send_header('Content-Type', 'audio/wav')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                group = groups[self.path]
                result = client.complete_payload(body, feature='core_or_dsh' if group == 'chat' else group, group=group)
                payload = result['response']
            except Exception as exc:
                return self.respond(400, {'error': {'message': str(exc) if isinstance(exc, ValueError) else '本地模型代理暂不可用'}})
            if not body.get('stream'):
                return self.respond(200, payload)
            choices, final = [], []
            for index, choice in enumerate(payload.get('choices', [])):
                delta = dict(choice.get('message', {}))
                if delta.get('tool_calls'):
                    delta['tool_calls'] = [dict(call, index=i) for i, call in enumerate(delta['tool_calls'])]
                choices.append({'index': index, 'delta': delta, 'finish_reason': None})
                final.append({'index': index, 'delta': {}, 'finish_reason': choice.get('finish_reason', 'stop')})
            common = {'id': payload.get('id', result['call_id']), 'object': 'chat.completion.chunk', 'model': payload.get('model', ''), 'created': payload.get('created', 0)}
            chunks = [{**common, 'choices': choices}, {**common, 'choices': final, 'usage': result['usage']}]
            raw = (''.join('data: ' + json.dumps(chunk, ensure_ascii=False) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass
    return Handler
