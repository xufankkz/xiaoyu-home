"""Synthetic functional tests. Never starts production or calls an external model."""
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import sqlite3
import unittest
from home_runtime.credentials import Credentials
from home_runtime.files import atomic_json, atomic_bytes, database
from home_runtime.model import ModelClient
from home_runtime.migration import Migration, seal

class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix='xiaoyu-release-test-')
        self.root = Path(self.temp.name)
        (self.root / 'release').mkdir()
        (self.root / 'dafeiyu/data/config').mkdir(parents=True)
        (self.root / 'dafeiyu/data/db').mkdir()
        atomic_bytes(self.root / 'dafeiyu/data/config/personality.md', b'')
        atomic_json(self.root / 'release/settings.json', {'profile': {}, 'daily_budget': 1, 'models': {'chat': {
            'endpoint': 'http://127.0.0.1:9999/v1', 'model': 'synthetic', 'input_price': 1, 'output_price': 2, 'key_optional': True}}})
        with database(self.root / 'dafeiyu/data/db/dafeiyu.db') as conn:
            conn.execute("CREATE TABLE memory_item(id TEXT PRIMARY KEY,content TEXT,summary TEXT DEFAULT '',layer TEXT DEFAULT 'instant',subtype TEXT DEFAULT 'chat',t_created TEXT DEFAULT '',tombstone INTEGER DEFAULT 0,origin TEXT DEFAULT '',ext TEXT DEFAULT '{}',status TEXT DEFAULT 'resolved')")
    def tearDown(self):
        self.temp.cleanup()
    def test_credentials_roundtrip_and_no_plaintext(self):
        store = Credentials(self.root / 'release/credentials.dpapi')
        store.set('sample', 'SYNTHETIC-SECRET-ONLY-FOR-TEST')
        self.assertEqual(store.get('sample'), 'SYNTHETIC-SECRET-ONLY-FOR-TEST')
        self.assertNotIn(b'SYNTHETIC-SECRET', store.path.read_bytes())
    def test_budget_and_usage_without_network(self):
        sent = []
        def transport(url, payload, key):
            sent.append(payload)
            return {'choices': [{'message': {'content': 'synthetic'}}], 'usage': {'prompt_tokens': 100, 'completion_tokens': 20}}
        client = ModelClient(self.root, transport)
        self.assertEqual(client.complete([{'role': 'user', 'content': 'test'}])['text'], 'synthetic')
        self.assertEqual(client.usage()['requests'], 1)
        self.assertAlmostEqual(client.usage()['estimated_cost'], 0.00014)
        config = client.settings(); config['daily_budget'] = 0
        atomic_json(self.root / 'release/settings.json', config)
        with self.assertRaises(ValueError):
            client.complete([{'role': 'user', 'content': 'test'}])
        self.assertEqual(len(sent), 1)
    def test_unconfirmed_model_call_keeps_reservation(self):
        def transport(*_):
            raise ValueError('synthetic disconnected response')
        client = ModelClient(self.root, transport)
        with self.assertRaises(ValueError):
            client.complete([{'role': 'user', 'content': 'test'}])
        self.assertGreater(client.usage()['estimated_cost'], 0)
        self.assertEqual(client.usage()['recent'][0]['status'], 'unconfirmed')
    def test_encrypted_migration_plan_incremental_deleted_and_idempotent(self):
        database_path = self.root / 'dafeiyu/data/db/dafeiyu.db'
        with database(database_path) as conn:
            conn.execute('INSERT INTO memory_item(id,content,tombstone) VALUES(?,?,?)', ('existing', 'original', 1))
        payload = {'format': 'xiaoyu-home', 'schema': 1, 'source': {'app': 'synthetic'}, 'documents': [],
                   'tables': {'memory_item': [{'id': 'existing', 'content': 'different', 'tombstone': 0},
                                              {'id': 'new', 'content': 'exact source text', 'tombstone': 1}]}}
        migration = Migration(self.root)
        raw = seal(json.dumps(payload).encode(), 'synthetic-password')
        self.assertNotIn(b'exact source text', raw)
        plan = migration.inspect(raw, password='synthetic-password')
        self.assertEqual(plan['additions']['memory_item'], 1)
        self.assertEqual(plan['conflicts_preserved']['memory_item'], 1)
        with database(database_path) as conn:
            before = list(conn.execute('SELECT * FROM memory_item'))
        self.assertEqual(len(before), 1)
        result = migration.apply(plan['plan_id'])
        self.assertEqual(result['imported']['memory_item'], 1)
        with database(database_path) as conn:
            self.assertEqual(conn.execute('SELECT content,tombstone FROM memory_item WHERE id=?', ('existing',)).fetchone(), ('original', 1))
            self.assertEqual(conn.execute('SELECT content,tombstone FROM memory_item WHERE id=?', ('new',)).fetchone(), ('exact source text', 1))
        plan2 = migration.inspect(raw, password='synthetic-password')
        self.assertEqual(migration.apply(plan2['plan_id'])['imported']['memory_item'], 0)
        self.assertTrue(Path(result['snapshot']['path']).is_file())
    def test_astrbot_adapter_preserves_source_and_deletion(self):
        source = self.root / 'source.db'
        with database(source) as conn:
            conn.execute('CREATE TABLE documents(doc_id TEXT,text TEXT,metadata TEXT)')
            conn.execute('INSERT INTO documents VALUES(?,?,?)', ('test-1', 'unchanged text', json.dumps({'deleted': True, 'date': '2026-01-01', 'session_id': 'synthetic-session'})))
        migration = Migration(self.root)
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        plan = migration.inspect(source.read_bytes(), kind='astrbot-livingmemory')
        migration.apply(plan['plan_id'])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
        with database(migration.database) as conn:
            content, deleted, meta = conn.execute('SELECT content,tombstone,ext FROM memory_item').fetchone()
            self.assertEqual((content, deleted), ('unchanged text', 1))
            self.assertEqual(json.loads(meta)['source_doc_id'], 'test-1')
    def test_chat_export_keeps_session_ownership(self):
        payload = {'sessions': [{'id': 'synthetic-session', 'messages': [{'id': 'm1', 'role': 'assistant', 'content': 'original reply', 'created_at': '2026-01-02T03:04:05', 'deleted': True}]}]}
        migration = Migration(self.root)
        plan = migration.inspect(json.dumps(payload).encode(), kind='dsh')
        migration.apply(plan['plan_id'])
        with database(migration.database) as conn:
            content, at, deleted, meta = conn.execute('SELECT content,t_created,tombstone,ext FROM memory_item').fetchone()
            self.assertEqual((content, at, deleted), ('original reply', '2026-01-02T03:04:05', 1))
            self.assertEqual(json.loads(meta)['conversation'], 'dsh:synthetic-session')

if __name__ == '__main__':
    unittest.main()
