"""Standalone local chat using the same persona and memory core as QQ."""
from pathlib import Path
import json
import sqlite3
import threading
import urllib.request
import uuid
from datetime import datetime
from .model import ModelClient
from .files import read_json, database
import release_paths

class LocalChat:
    def __init__(self, workspace, model=None):
        self.workspace = Path(workspace)
        self.db = self.workspace / 'release/chat.sqlite'
        self.model = model or ModelClient(workspace)
        self.lock = threading.Lock()
        self.memory_lock = threading.Lock()
        self.next_flush = 0
        with database(self.db) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS turns (id TEXT PRIMARY KEY, user_text TEXT, reply TEXT, status TEXT, created_at TEXT, error TEXT)')
            conn.execute('CREATE TABLE IF NOT EXISTS memory_queue (id TEXT PRIMARY KEY, user_text TEXT, reply TEXT, saved INTEGER DEFAULT 0)')
            conn.execute("UPDATE turns SET status='unconfirmed',error='上次运行在等待模型时结束；为避免重复计费，不会自动重发。' WHERE status='pending'")

    def history(self):
        with database(self.db) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute('SELECT * FROM turns ORDER BY created_at DESC LIMIT 100')][::-1]

    def core(self, route, body):
        token = release_paths.runtime()['internal_token']
        request = urllib.request.Request(release_paths.url('core', route), method='POST',
                                         data=json.dumps(body, ensure_ascii=False).encode(),
                                         headers={'Content-Type': 'application/json', 'X-Xiaoyu-Internal': token})
        with urllib.request.urlopen(request, timeout=25) as response:
            result = json.load(response)
            if result.get("ok") is False:
                raise ValueError("本地记忆核心暂未完成保存。")
            return result

    def send(self, body):
        text = str(body.get('text') or '').strip()
        identifier = str(body.get('request_id') or '')
        if not text or len(text) > 12000 or not identifier or len(identifier) > 80:
            raise ValueError('请输入 1–12000 字的消息。')
        if not self.lock.acquire(blocking=False):
            raise ValueError('正在等待上一条消息，请稍后。')
        try:
            with database(self.db) as conn:
                conn.row_factory = sqlite3.Row
                existing = conn.execute('SELECT * FROM turns WHERE id=?', (identifier,)).fetchone()
                if existing:
                    return dict(existing)
                conn.execute('INSERT INTO turns VALUES(?,?,?,?,?,?)', (identifier, text, '', 'pending', datetime.now().isoformat(), ''))
            try:
                conversation = 'local:owner'
                context = self.core('/api/context/prepare', {'conversation': conversation, 'include_memory': True,
                    'allow_lookup': False, 'messages': [{'text': text, 'messageId': identifier, 'userId': 'local-owner'}]})
                persona = (self.workspace / 'dafeiyu/data/config/personality.md').read_text(encoding='utf-8')
                if not persona.strip():
                    raise ValueError('请先写入角色人设，或迁入你的角色。')
                system = '这是用户知情使用的本地 AI 角色软件。保持角色的独立观点和自然表达，回答依据实际经历和已确认记忆；不编造做过的事或工具结果。\n\n' + persona + '\n\n' + context.get('block', '')
                messages = [{'role': 'system', 'content': system}]
                for turn in self.history()[-21:]:
                    if turn['id'] == identifier or turn['status'] != 'completed':
                        continue
                    messages.extend(({'role': 'user', 'content': turn['user_text']}, {'role': 'assistant', 'content': turn['reply']}))
                messages.append({'role': 'user', 'content': text})
                result = self.model.complete(messages, feature='local_chat')
                reply = result['text']
                with database(self.db) as conn:
                    conn.execute('UPDATE turns SET reply=?,status=? WHERE id=?', (reply, 'completed', identifier))
                    conn.execute('INSERT OR IGNORE INTO memory_queue(id,user_text,reply) VALUES(?,?,?)', (identifier, text, reply))
                threading.Thread(target=self.flush_memories, name="local-memory-flush", daemon=True).start()
            except Exception as exc:
                message = str(exc) if isinstance(exc, ValueError) else '本次聊天未能完成，请检查模型与本地核心状态。'
                with database(self.db) as conn:
                    conn.execute('UPDATE turns SET status=?,error=? WHERE id=?', ('failed', message, identifier))
            return next(turn for turn in self.history() if turn['id'] == identifier)
        finally:
            self.lock.release()

    def flush_memories(self):
        if not self.memory_lock.acquire(blocking=False):
            return
        try:
            self._flush_memories()
        finally:
            self.memory_lock.release()

    def _flush_memories(self):
        with database(self.db) as conn:
            queued = conn.execute('SELECT id,user_text,reply,saved FROM memory_queue WHERE saved<2 ORDER BY rowid LIMIT 20').fetchall()
        for identifier, text, reply, saved in queued:
            try:
                if saved == 0:
                    self.core('/api/chat/record', {'conversation': 'local:owner', 'content': reply,
                        'event_id': 'local:owner:' + identifier, 'is_self': True, 'sender': '自己', 'user_id': 'self', 'is_owner': False})
                    with database(self.db) as conn:
                        conn.execute('UPDATE memory_queue SET saved=1 WHERE id=?', (identifier,))
                self.core('/api/thought/turn', {'conv': 'local:owner', 'user_text': text, 'reply_text': reply,
                    'context_prepared': True, 'event_id': 'local:owner:' + identifier, 'decision': '本地聊天'})
                with database(self.db) as conn:
                    conn.execute('UPDATE memory_queue SET saved=2 WHERE id=?', (identifier,))
            except Exception:
                # A successfully paid-for reply is durable even if memory is offline.
                break
