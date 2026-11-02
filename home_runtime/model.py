"""Shared paid-request ledger. Reserve a conservative estimate before dispatch."""
from datetime import datetime
from pathlib import Path
import json
import math
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from .credentials import Credentials
from .files import read_json, database

def endpoint(value):
    if not isinstance(value, str) or len(value) > 1000:
        raise ValueError('模型地址无效')
    value = value.strip().rstrip('/')
    url = urllib.parse.urlsplit(value)
    if (url.scheme not in ('https', 'http') or not url.hostname or url.username or url.password
        or url.query or url.fragment or (url.scheme == 'http' and url.hostname not in ('127.0.0.1', 'localhost', '::1'))):
        raise ValueError('请填写 HTTPS API 根地址，或本机 HTTP API 根地址。')
    if url.path.endswith('/chat/completions'):
        raise ValueError('请填写 API 根地址，不包含 /chat/completions。')
    return value

def number(value, high=1000000):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= high:
        raise ValueError('价格或预算无效')
    return float(value)

class ModelClient:
    def __init__(self, workspace, transport=None):
        self.workspace = Path(workspace)
        self.db = self.workspace / 'release/calls.sqlite'
        self.secrets = Credentials(self.workspace / 'release/credentials.dpapi')
        self.lock = threading.RLock()
        self.transport = transport or self._transport
        self.db.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, day TEXT, feature TEXT, status TEXT, reserved REAL, actual REAL, input_tokens INTEGER, output_tokens INTEGER, created_at TEXT)')

    def connect(self):
        return database(self.db, timeout=10)

    def settings(self):
        return read_json(self.workspace / 'release/settings.json')

    def usage(self):
        today = datetime.now().strftime('%Y-%m-%d')
        with self.connect() as conn:
            row = conn.execute('SELECT COUNT(*), COALESCE(SUM(COALESCE(actual,reserved)),0), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0) FROM calls WHERE day=?', (today,)).fetchone()
            items = conn.execute('SELECT feature,status,reserved,actual,created_at FROM calls WHERE day=? ORDER BY created_at DESC LIMIT 30', (today,)).fetchall()
        return {'day': today, 'requests': row[0], 'estimated_cost': row[1], 'input_tokens': row[2], 'output_tokens': row[3],
                'daily_budget': self.settings().get('daily_budget', 0),
                'recent': [dict(zip(('feature', 'status', 'reserved', 'actual', 'created_at'), item)) for item in items],
                'note': '按用户填写的单价估算；已发出但未确认的请求保留预留额度。实际账单以服务商为准。'}

    def reserve(self, feature, payload, model):
        budget = number(self.settings().get('daily_budget', 0))
        if not budget:
            raise ValueError('请先设置每日模型预算和输入、输出单价；预算为 0 时不发出收费请求。')
        # UTF-8 bytes + overhead is deliberately conservative for admission control.
        input_bound = len(json.dumps(payload, ensure_ascii=False).encode()) + 2048
        max_tokens = int(payload.get('max_tokens', 2048))
        reserved = (input_bound * number(model['input_price']) + max_tokens * number(model['output_price'])) / 1000000
        call_id, day = uuid.uuid4().hex, datetime.now().strftime('%Y-%m-%d')
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            used = conn.execute('SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM calls WHERE day=?', (day,)).fetchone()[0]
            if used + reserved > budget:
                raise ValueError('今日模型预算不足以预留本次请求，已停止发送。')
            conn.execute('INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?)',
                         (call_id, day, feature, 'reserved', reserved, None, 0, 0, datetime.now().isoformat()))
        return call_id

    def reserve_fixed(self, feature, cost):
        cost = number(cost, 10000)
        budget = number(self.settings().get('daily_budget', 0))
        identifier, day = uuid.uuid4().hex, datetime.now().strftime('%Y-%m-%d')
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            used = conn.execute('SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM calls WHERE day=?', (day,)).fetchone()[0]
            if cost and (not budget or used + cost > budget):
                raise ValueError('今日预算不足以预留本次请求，已停止发送。')
            conn.execute('INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?)',
                (identifier, day, feature, 'reserved', cost, None, 0, 0, datetime.now().isoformat()))
        return identifier

    def finish_fixed(self, identifier, success):
        with self.connect() as conn:
            conn.execute('UPDATE calls SET status=?,actual=CASE WHEN ? THEN reserved ELSE NULL END WHERE id=?',
                ('completed' if success else 'unconfirmed', int(success), identifier))

    @staticmethod
    def _transport(url, payload, key):
        request = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode(), method='POST',
                                         headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key,
                                                  'User-Agent': 'XiaoyuHome/0.2.0-beta.1'})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError('模型响应超过上限')
            return json.loads(raw)
        except urllib.error.HTTPError as exc:
            raise ValueError('模型接口返回 HTTP ' + str(exc.code) + '，请核对地址、模型和凭据。') from None
        except (OSError, json.JSONDecodeError):
            raise ValueError('本次模型请求未能确认；为避免重复计费，不会自动重发。') from None

    def complete(self, messages, feature='chat', group='chat', max_tokens=2048):
        result = self.complete_payload({'messages': messages, 'max_tokens': max_tokens}, feature=feature, group=group)
        content = result['response'].get('choices', [{}])[0].get('message', {}).get('content')
        if not isinstance(content, str) or not content.strip():
            raise ValueError('模型没有返回可显示的正文。')
        result['text'] = content.strip()
        return result

    def complete_payload(self, request, feature='chat', group='chat'):
        config = self.settings().get('models', {}).get(group, {})
        if not config.get('endpoint') or not config.get('model'):
            raise ValueError('请先在首次配置中填写模型地址和模型名。')
        key = self.secrets.get('model:' + group)
        if not key and not config.get('key_optional'):
            raise ValueError('请在本机填写模型 API Key。')
        if not isinstance(request.get('messages'), list):
            raise ValueError('消息格式无效')
        allowed = ('messages', 'tools', 'tool_choice', 'response_format', 'temperature', 'top_p', 'frequency_penalty',
                   'presence_penalty', 'stop', 'parallel_tool_calls', 'reasoning_effort')
        payload = {name: request[name] for name in allowed if name in request}
        payload.update(model=config['model'], max_tokens=max(1, min(8192, int(request.get('max_completion_tokens') or request.get('max_tokens') or 2048))), stream=False)
        call_id = self.reserve(feature, payload, config)
        try:
            data = self.transport(endpoint(config['endpoint']) + '/chat/completions', payload, key)
            message = data.get('choices', [{}])[0].get('message', {})
            if not message.get('content') and not message.get('tool_calls'):
                raise ValueError('模型没有返回正文或工具调用。')
            usage = data.get('usage', {})
            inputs, outputs = int(usage.get('prompt_tokens', 0)), int(usage.get('completion_tokens', 0))
            actual = ((inputs * number(config['input_price']) + outputs * number(config['output_price'])) / 1000000) if usage else None
            with self.connect() as conn:
                conn.execute('UPDATE calls SET status=?,actual=?,input_tokens=?,output_tokens=? WHERE id=?', ('completed', actual, inputs, outputs, call_id))
            return {'response': data, 'usage': usage, 'call_id': call_id}
        except Exception:
            with self.connect() as conn:
                conn.execute('UPDATE calls SET status=? WHERE id=?', ('unconfirmed', call_id))
            raise
