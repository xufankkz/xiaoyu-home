"""Asynchronous snapshot inspection and isolated restore rehearsal. No live writes."""
import hashlib
import json
import shutil
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from contextlib import closing


class RecoveryPreview:
    def __init__(self, root, core):
        self.root, self.core = Path(root), Path(core)
        self.lock = threading.Lock()
        self.job = {'running': False}

    def backups(self):
        rows = []
        for folder in (self.root / '_backup').glob('*'):
            try:
                data = json.loads((folder / 'manifest.json').read_text(encoding='utf-8-sig'))
                if data.get('complete') is not True or data.get('kind') != 'business-source-and-data':
                    continue
                rows.append({'id': folder.name, 'created_at': data.get('created_at', ''),
                             'files': len(data['files']), 'bytes': sum(r.get('bytes', 0) for r in data['files'])})
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return {'items': sorted(rows, key=lambda r: r['created_at'], reverse=True)[:40], 'job': self.status()}

    def status(self):
        with self.lock:
            return json.loads(json.dumps(self.job))

    def start(self, backup_id, rehearse=False):
        if backup_id not in {r['id'] for r in self.backups()['items']}:
            raise ValueError('请选择列表中的完整业务备份')
        with self.lock:
            if self.job.get('running'):
                raise ValueError('已有预览或副本核验正在执行，请等待完成')
            self.job = {'running': True, 'backup_id': backup_id, 'rehearse': rehearse,
                        'started_at': datetime.now().isoformat(timespec='seconds')}
        threading.Thread(target=self._work, args=(backup_id, rehearse), daemon=True).start()
        return self.status()

    @staticmethod
    def _digest(path):
        h = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(block)
        return h.hexdigest()

    @staticmethod
    def _database(path):
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as conn:
            if conn.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise ValueError('备份数据库完整性核验未通过')
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            return {name: conn.execute('SELECT COUNT(*) FROM "' + name + '"').fetchone()[0]
                    for name in ('memory_item', 'knowledge_item', 'affinity_contact_day') if name in names}

    def _work(self, backup_id, rehearse):
        try:
            folder = (self.root / '_backup' / backup_id).resolve()
            data = json.loads((folder / 'manifest.json').read_text(encoding='utf-8-sig'))
            if data.get('complete') is not True:
                raise ValueError('备份不完整')
            staging = self.root / '_restore-preview' / uuid4().hex if rehearse else None
            if staging:
                staging.mkdir(parents=True, exist_ok=False)
            changes, databases, verified = [], [], 0
            # Only display file names and counts, never configuration/key values.
            for row in data['files']:
                live_path = Path(row['source']).resolve()
                # External profile files are verified from the snapshot only.
                compare_live = live_path.is_relative_to(self.root.resolve()) or live_path.is_relative_to(self.core.resolve())
                relative = Path(row['path'])
                source = (folder / relative).resolve()
                if not source.is_relative_to(folder) or source == folder:
                    raise ValueError('备份文件目录不符合预期')
                if source.stat().st_size != row['bytes'] or self._digest(source) != row['sha256']:
                    raise ValueError('备份文件校验未通过，请选择其他完整备份')
                if row['method'] == 'sqlite-backup':
                    counts = self._database(source)
                    if counts:
                        current = {}
                        live = Path(row['source'])
                        if live.resolve() == (self.core / 'data/db/dafeiyu.db').resolve() and live.exists():
                            with closing(sqlite3.connect(live.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
                                for name in counts:
                                    try: current[name] = conn.execute('SELECT COUNT(*) FROM "' + name + '"').fetchone()[0]
                                    except sqlite3.Error: current[name] = None
                        databases.append({'file': row['path'], 'backup_counts': counts, 'current_counts': current})
                elif len(changes) < 120:
                    live = Path(row['source'])
                    if compare_live and live.is_file() and self._digest(live) != row['sha256']:
                        changes.append({'file': row['path'], 'status': '与当前不同'})
                    elif compare_live and not live.exists():
                        changes.append({'file': row['path'], 'status': '当前不存在'})
                if staging:
                    target = (staging / relative).resolve()
                    if not target.is_relative_to(staging.resolve()):
                        raise ValueError('副本目录不符合预期')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    if self._digest(target) != row['sha256']:
                        raise ValueError('独立副本校验失败')
                verified += 1
            result = {'running': False, 'ok': True, 'backup_id': backup_id, 'rehearse': rehearse,
                      'verified_files': verified, 'databases': databases, 'differences': changes,
                      'staging': str(staging) if staging else '', 'finished_at': datetime.now().isoformat(timespec='seconds'),
                      'note': '独立副本已核验，未启动服务，当前数据和设定保持。' if staging else '预览已完成；差异只供核对，恢复旧备份会影响当前新增数据与设定。'}
        except Exception:
            result = {'running': False, 'ok': False, 'backup_id': backup_id, 'rehearse': rehearse,
                      'error': '预览或副本核验未完成，请检查备份完整性及可用磁盘空间。当前数据未覆盖。'}
        with self.lock:
            self.job = result

