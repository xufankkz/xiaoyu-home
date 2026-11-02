"""Encrypted, provenance-preserving migration with read-only plans and verified snapshots."""
from pathlib import Path, PurePosixPath
from datetime import datetime
from dataclasses import fields
import base64
import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import threading
import zipfile
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from . import VERSION
from .files import atomic_bytes, atomic_json, read_json, database
from contextlib import contextmanager

MAGIC = b'XIAOYU-FISH-1\0'
LIMIT = 128 * 1024 * 1024
_lock = threading.RLock()
DOCUMENT_DIRS = ('diary', 'memory', '记忆系统', 'state', 'learning/reports')

def document_path(workspace, value):
    relative = PurePosixPath(value)
    allowed = any(str(relative).startswith('dafeiyu/data/' + name + '/') for name in DOCUMENT_DIRS)
    if (relative.is_absolute() or '..' in relative.parts or not allowed or ':' in value or '\\' in value
        or relative.suffix.lower() not in ('.md', '.txt', '.json')):
        raise ValueError('迁移文档路径不在支持的数据目录内。')
    root = Path(workspace).resolve()
    target = (root / value).resolve()
    if not target.is_relative_to(root):
        raise ValueError('迁移文档路径超出本实例。')
    return target

def checksum(raw):
    return hashlib.sha256(raw).hexdigest()

def derive(password, salt):
    if not isinstance(password, str) or len(password) < 8 or len(password) > 1024:
        raise ValueError('迁移包密码需要 8–1024 个字符。')
    return Scrypt(salt=salt, length=32, n=32768, r=8, p=1).derive(password.encode())

def seal(raw, password):
    salt, nonce = os.urandom(16), os.urandom(12)
    return MAGIC + salt + nonce + AESGCM(derive(password, salt)).encrypt(nonce, raw, MAGIC)

def open_package(raw, password):
    if len(raw) > LIMIT or not raw.startswith(MAGIC):
        raise ValueError('请选择支持的小鱼之家加密迁移包。')
    header = len(MAGIC)
    salt, nonce, encrypted = raw[header:header+16], raw[header+16:header+28], raw[header+28:]
    try:
        decoded = AESGCM(derive(password, salt)).decrypt(nonce, encrypted, MAGIC)
    except ValueError:
        raise
    except Exception:
        raise ValueError('迁移包密码不正确，或文件完整性校验未通过。') from None
    try:
        payload = json.loads(decoded)
        if payload.get('format') != 'xiaoyu-home' or payload.get('schema') != 1:
            raise ValueError()
        return payload
    except (ValueError, AttributeError):
        raise ValueError('迁移包格式或版本不受支持。') from None

@contextmanager
def readonly(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def verified_snapshot(source, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with readonly(source) as src, database(target) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('快照数据库未通过一致性核验。')
    return {'path': str(target), 'sha256': checksum(target.read_bytes())}

def table_rows(db_path):
    result = {}
    with readonly(db_path) as conn:
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            name = row[0]
            if not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', name) or name in ('meta', 'schema_meta'):
                continue
            records = [dict(item) for item in conn.execute('SELECT * FROM "' + name + '"')]
            if any(isinstance(value, bytes) for record in records for value in record.values()):
                continue
            result[name] = records
    return result

class Migration:
    def __init__(self, workspace):
        self.workspace = Path(workspace)
        self.database = self.workspace / 'dafeiyu/data/db/dafeiyu.db'
        self.pending = {}

    def export(self, password):
        with _lock:
            with tempfile.TemporaryDirectory(prefix='xiaoyu-export-') as temp:
                snapshot = Path(temp) / 'core.sqlite'
                verified_snapshot(self.database, snapshot)
                tables = table_rows(snapshot)
            profile = read_json(self.workspace / 'release/settings.json').get('profile', {})
            persona = (self.workspace / 'dafeiyu/data/config/personality.md').read_text(encoding='utf-8')
            documents = []
            core = self.workspace / 'dafeiyu/data'
            for directory in DOCUMENT_DIRS:
                for source in (core / directory).rglob('*'):
                    if not source.is_file() or source.suffix.lower() not in ('.md', '.txt', '.json') or source.stat().st_size > 8 * 1024 * 1024:
                        continue
                    if any(part in ('chat-inbox', 'memory-inbox', 'web-profile', 'web-cache', 'logs') for part in source.relative_to(core).parts):
                        continue
                    relative = source.relative_to(core).as_posix()
                    raw = source.read_bytes()
                    documents.append({'path': 'dafeiyu/data/' + relative, 'data': base64.b64encode(raw).decode(), 'sha256': checksum(raw)})
            payload = {'format': 'xiaoyu-home', 'schema': 1, 'version': VERSION, 'created_at': datetime.now().isoformat(),
                       'source': {'app': 'xiaoyu-home', 'version': VERSION}, 'tables': tables,
                       'profile': profile, 'persona': persona, 'documents': documents,
                       'appearance': read_json(self.workspace / 'xiaoyu-web/data/settings.json').get('appearance', {}),
                       'credentials_included': False}
            payload['chat'] = []
            chat_db = self.workspace / 'release/chat.sqlite'
            if chat_db.is_file():
                with readonly(chat_db) as conn:
                    payload['chat'] = [dict(row) for row in conn.execute('SELECT * FROM turns')]
            payload['aliases'] = read_json(self.workspace / 'astrbot/data/plugins/astrbot_plugin_xiaoyu_alias/aliases.json')
            raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
            if len(raw) > LIMIT - 1024:
                raise ValueError('迁移内容超过此测试版 128 MiB 上限，请先分开归档大文件。')
            return seal(raw, password)

    def adapt_astrbot_livingmemory(self, raw):
        with tempfile.TemporaryDirectory(prefix='xiaoyu-astrbot-') as temp:
            path = Path(temp) / 'livingmemory.db'
            path.write_bytes(raw)
            with readonly(path) as conn:
                columns = {row[1] for row in conn.execute('PRAGMA table_info(documents)')}
                if not {'doc_id', 'text', 'metadata'} <= columns:
                    raise ValueError('仅支持含 doc_id/text/metadata 的 AstrBot livingmemory 数据库；未识别的插件数据保持原样。')
                memories = []
                for row in conn.execute('SELECT doc_id,text,metadata FROM documents'):
                    metadata = json.loads(row['metadata'] or '{}')
                    source_id = str(row['doc_id'])
                    text = str(row['text'] or '')
                    memories.append({'id': 'astrbot-livingmemory-' + checksum(source_id.encode())[:24], 'layer': 'long2', 'subtype': 'summary',
                        'content': text, 'summary': str(metadata.get('canonical_summary') or text[:100]),
                        't_created': str(metadata.get('source_time_start') or metadata.get('date') or ''),
                        'tombstone': int(bool(metadata.get('deleted') or metadata.get('tombstone'))), 'origin': 'astrbot_livingmemory',
                        'ext': json.dumps({'source_app': 'astrbot', 'source_doc_id': source_id, 'source_metadata': metadata,
                                           'source_sha256': checksum(text.encode()), 'conversation': metadata.get('session_id', '')}, ensure_ascii=False)})
        return {'format': 'xiaoyu-home', 'schema': 1, 'source': {'app': 'astrbot-livingmemory', 'schema': 'documents/doc_id-text-metadata'},
                'tables': {'memory_item': memories}, 'documents': [], 'profile': {}, 'persona': ''}

    def adapt_chat_export(self, raw, source):
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise ValueError('请选择文档说明中的 DSH/AstrBot JSON 对话导出文件。') from None
        sessions = data.get('sessions') if isinstance(data, dict) else None
        if not isinstance(sessions, list):
            raise ValueError('导出文件需要 sessions 数组；不能猜测未知版本的会话目录。')
        rows = []
        for session in sessions:
            if not isinstance(session, dict) or not isinstance(session.get('messages'), list):
                raise ValueError('对话导出格式不完整')
            conversation = str(session.get('id') or '')
            if not conversation:
                raise ValueError('每个会话需要原始 id')
            for index, message in enumerate(session['messages']):
                if not isinstance(message, dict) or not isinstance(message.get('content'), str):
                    raise ValueError('仅支持具有 content 文本的导出消息，媒体请保留原文件')
                source_id = str(message.get('id') or index)
                text = message['content']
                rows.append({'id': 'import-' + checksum((source + ':' + conversation + ':' + source_id).encode())[:32],
                    'layer': 'instant', 'subtype': 'chat', 'content': text, 'summary': text[:100],
                    't_created': str(message.get('created_at') or message.get('timestamp') or ''),
                    'tombstone': int(bool(message.get('deleted') or message.get('tombstone'))), 'status': 'resolved', 'origin': source + '_export',
                    'ext': json.dumps({'source_app': source, 'source_id': source_id, 'conversation': source + ':' + conversation,
                                       'source_conversation': conversation, 'source_sha256': checksum(text.encode()),
                                       'is_self': message.get('role') == 'assistant', 'source_message': message}, ensure_ascii=False)})
        return {'format': 'xiaoyu-home', 'schema': 1, 'source': {'app': source, 'schema': 'sessions/messages-v1'},
                'tables': {'memory_item': rows}, 'documents': [], 'profile': data.get('profile', {}), 'persona': data.get('persona', '')}

    def adapt_dsh_native(self, raw):
        import subprocess
        from .instance import source_root
        component = self.workspace.parent / 'components/dsh'
        if not (component / 'node_modules/@deepseek-ai/dsh-session-format-catalog/lib/index.js').is_file():
            raise ValueError('读取 DSH 原生会话需要先安装 DSH 组件。')
        if raw.startswith(b'\x28\xb5\x2f\xfd'):
            import zstandard
            with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw)) as reader:
                raw = reader.read(LIMIT + 1)
            if len(raw) > LIMIT:
                raise ValueError('解压后的会话超过本版本上限。')
        result = subprocess.run([str(source_root() / 'runtime/node/node.exe'), str(source_root() / 'home_runtime/dsh_import.mjs')],
            input=json.dumps({'component': str(component), 'text': raw.decode('utf-8-sig')}).encode(),
            capture_output=True, timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            parsed = json.loads(result.stdout)
        except ValueError:
            raise ValueError('DSH 原生会话读取器未能运行，请核对组件安装。') from None
        if not parsed.get('ok'):
            raise ValueError(parsed.get('error', 'DSH 格式不受支持'))
        artifact = parsed['artifact']
        conversation = artifact['header']['id']
        messages, superseded = [], set()
        for event in artifact['events']:
            operation = event.get('surfaceOp')
            if isinstance(operation, dict) and operation.get('op') == 'replace':
                superseded.update(range(operation['startSeq'], operation['endSeq'] + 1))
            if event['type'] not in ('user/message', 'assistant/message'):
                continue
            message = event['data'] if event['type'] == 'user/message' else event['data'].get('message', {})
            text = '\n'.join(block['text'] for block in message.get('content', []) if block.get('type') == 'text' and isinstance(block.get('text'), str))
            if not text:
                continue
            messages.append({'id': str(message.get('id') or event['seq']), 'role': 'user' if event['type'] == 'user/message' else 'assistant',
                'content': text, 'created_at': datetime.fromtimestamp(event['time'] / 1000).isoformat(),
                'source_event': event, 'seq': event['seq']})
        for message in messages:
            message['deleted'] = message['seq'] in superseded
        data = {'sessions': [{'id': conversation, 'messages': messages}]}
        payload = self.adapt_chat_export(json.dumps(data, ensure_ascii=False).encode(), 'dsh')
        payload['source'] = {'app': 'dsh', 'schema': 'official-session-codecs', 'source_version': artifact['header']['version'],
                             'note': '文字消息进入记忆；工具运行状态、媒体二进制和插件状态保留在原 DSH，不作为新任务恢复。'}
        return payload

    def inspect(self, raw, kind='fish', password=''):
        if len(raw) > LIMIT:
            raise ValueError('迁移文件超过 128 MiB 上限')
        if kind == 'fish':
            payload = open_package(raw, password)
        elif kind == 'astrbot-livingmemory':
            payload = self.adapt_astrbot_livingmemory(raw)
        elif kind in ('dsh', 'astrbot'):
            payload = self.adapt_chat_export(raw, kind)
        elif kind == 'dsh-native':
            payload = self.adapt_dsh_native(raw)
        else:
            raise ValueError('不支持的迁入来源')
        plan = self.plan(payload)
        identifier = checksum(os.urandom(32))
        self.pending[identifier] = {'payload': payload, 'source_sha256': checksum(raw), 'plan': plan}
        return {'plan_id': identifier, 'source_sha256': checksum(raw), **plan}

    def plan(self, payload):
        additions, existing, conflicts, unsupported = {}, {}, {}, []
        with readonly(self.database) as conn:
            allowed = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table, records in payload.get('tables', {}).items():
                if table not in allowed or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', table):
                    unsupported.append(table)
                    continue
                primary = [row[1] for row in conn.execute('PRAGMA table_info("' + table + '")') if row[5]]
                if not primary:
                    unsupported.append(table)
                    continue
                additions[table] = existing[table] = conflicts[table] = 0
                for record in records:
                    if not isinstance(record, dict) or not all(key in record for key in primary):
                        raise ValueError('迁入记录缺少原始主键')
                    where = ' AND '.join('"' + key + '"=?' for key in primary)
                    old = conn.execute('SELECT * FROM "' + table + '" WHERE ' + where, [record[key] for key in primary]).fetchone()
                    if old is None:
                        additions[table] += 1
                    elif all(key in old.keys() and old[key] == value for key, value in record.items()):
                        existing[table] += 1
                    else:
                        conflicts[table] += 1
        documents = payload.get('documents', [])
        for document in documents:
            document_path(self.workspace, document['path'])
            raw = base64.b64decode(document['data'], validate=True)
            if len(raw) > 8 * 1024 * 1024 or checksum(raw) != document['sha256']:
                raise ValueError('迁入文档完整性校验未通过')
        return {'source': payload.get('source', {}), 'additions': additions, 'existing': existing, 'conflicts_preserved': conflicts,
                'unsupported_tables': unsupported, 'documents': len(documents), 'has_persona': bool(payload.get('persona')),
                'rule': '仅增量补齐缺失记录和文件；既有记录、删除状态、人设及设置保持原样。'}

    def apply(self, identifier):
        with _lock:
            pending = self.pending.get(identifier)
            if not pending:
                raise ValueError('迁入计划已失效，请重新选择文件并预览。')
            payload = pending['payload']
            current_plan = self.plan(payload)
            if current_plan != pending['plan']:
                raise ValueError('本地数据在预览后有变化，请重新预览。')
            folder = self.workspace / 'release/migration-backups' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            snapshot = verified_snapshot(self.database, folder / 'core.sqlite')
            from .settings import Settings
            config_store = Settings(self.workspace)
            config_store.snapshot([config_store.path, self.workspace / 'dafeiyu/data/config/personality.md',
                self.workspace / 'xiaoyu-web/data/settings.json', self.workspace / 'astrbot/data/plugins/astrbot_plugin_xiaoyu_alias/aliases.json'])
            journal = {'state': 'prepared', 'source_sha256': pending['source_sha256'], 'added_files': [], 'snapshot': snapshot}
            atomic_json(folder / 'journal.json', journal)
            imported = {}
            with database(self.database, timeout=20) as conn:
                conn.execute('BEGIN IMMEDIATE')
                allowed = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table, records in payload.get('tables', {}).items():
                    if table not in allowed or table in current_plan['unsupported_tables']:
                        continue
                    columns = {row[1] for row in conn.execute('PRAGMA table_info("' + table + '")')}
                    count = 0
                    for record in records:
                        selected = {key: value for key, value in record.items() if key in columns}
                        names = ','.join('"' + key + '"' for key in selected)
                        marks = ','.join('?' for _ in selected)
                        inserted = conn.execute('INSERT OR IGNORE INTO "' + table + '" (' + names + ') VALUES (' + marks + ')', list(selected.values())).rowcount
                        count += inserted
                        if inserted and table == 'memory_item' and selected.get('id') and not selected.get('tombstone') and 'inverted_index' in allowed:
                            from core.text import bigrams
                            text = ' '.join(str(selected.get(name, '')) for name in ('summary', 'content', 'skeleton'))
                            conn.executemany('INSERT OR IGNORE INTO inverted_index(token,owner,owner_kind,tf) VALUES(?,?,?,?)',
                                [(token, selected['id'], 'memory', 1) for token in bigrams(text)])
                    imported[table] = count
                if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('迁入后数据库核验失败，事务已回滚。')
            journal['state'] = 'database_committed'
            atomic_json(folder / 'journal.json', journal)
            added_files = []
            for document in payload.get('documents', []):
                target = document_path(self.workspace, document['path'])
                if not target.exists():
                    atomic_bytes(target, base64.b64decode(document['data']))
                    added_files.append(document['path'])
                    journal['added_files'] = added_files
                    atomic_json(folder / 'journal.json', journal)
            # New empty instances may adopt an imported identity. Never replace an existing persona.
            persona = self.workspace / 'dafeiyu/data/config/personality.md'
            blank_instance = not persona.read_text(encoding='utf-8').strip() and not read_json(self.workspace / 'release/settings.json').get('profile', {}).get('name')
            if not persona.read_text(encoding='utf-8').strip() and payload.get('persona'):
                store = Settings(self.workspace)
                store.save({'revision': store.revision(), 'persona': payload['persona'], 'profile': payload.get('profile', {})})
            # Local messages have their own immutable request ids; never resend them.
            chat_db = self.workspace / 'release/chat.sqlite'
            if payload.get('chat') and chat_db.is_file():
                verified_snapshot(chat_db, folder / 'chat.sqlite')
                with database(chat_db) as conn:
                    for row in payload['chat']:
                        conn.execute('INSERT OR IGNORE INTO turns(id,user_text,reply,status,created_at,error) VALUES(?,?,?,?,?,?)',
                            [row.get(key, '') for key in ('id','user_text','reply','status','created_at','error')])
            aliases_path = self.workspace / 'astrbot/data/plugins/astrbot_plugin_xiaoyu_alias/aliases.json'
            aliases = read_json(aliases_path)
            for key, value in payload.get('aliases', {}).items():
                if re.fullmatch(r'\d{5,12}', key) and isinstance(value, str) and len(value) <= 60:
                    aliases.setdefault(key, value)
            if aliases:
                atomic_json(aliases_path, aliases)
            appearance_path = self.workspace / 'xiaoyu-web/data/settings.json'
            preferences = read_json(appearance_path)
            if not preferences.get('migration_adopted') and payload.get('appearance') and blank_instance:
                from product_settings import UI_THEMES, UI_COLOR_MODES, UI_NIGHT_PALETTES
                appearance = payload['appearance']
                if appearance.get('theme') in UI_THEMES and appearance.get('color_mode') in UI_COLOR_MODES and appearance.get('night_palette') in UI_NIGHT_PALETTES:
                    preferences['appearance'] = appearance
                    preferences['migration_adopted'] = True
                    atomic_json(appearance_path, preferences)
            result = {'imported': imported, 'added_files': len(added_files), 'snapshot': snapshot,
                      'source_sha256': pending['source_sha256'], 'source': payload.get('source'), 'existing_records_preserved': True}
            atomic_json(folder / 'result.json', result)
            journal['state'] = 'completed'
            atomic_json(folder / 'journal.json', journal)
            atomic_json(self.workspace / 'release/control.json', {'action': 'migration-reload', 'id': identifier})
            self.pending.pop(identifier, None)
            return result
