# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""大肥鱼 · 数据库连接与事务。

一条铁律：SQL 只出现在 core/repo/ 里，其它模块一律走仓储函数。
并发模型：线程本地连接 + 一把写锁（WAL 下读不阻塞读，写串行）。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

# 与 core/settings.py 保持一致：默认落在程序目录下的 data\，
# 避免原版 "~/.dafeiyu" 未 expanduser 时随工作目录漂移
ROOT = Path(
    os.environ.get("DAFEIYU_HOME") or (Path(__file__).resolve().parents[1] / "data")
).resolve()
DB_DIR = ROOT / "db"
DB_PATH = DB_DIR / "dafeiyu.db"
BACKUP_DIR = DB_DIR / "backup"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")

SCHEMA_VERSION = 18

# v3 之后新增的列：老库靠 ALTER 补，新库靠 schema.sql 建。两边都幂等。
_EXTRA_COLUMNS = (
    ("memory_item", "confidence", 'REAL NOT NULL DEFAULT 1.0'),
    ("memory_item", "valid_from", "TEXT NOT NULL DEFAULT ''"),
    ("memory_item", "valid_until", "TEXT NOT NULL DEFAULT ''"),
    ("memory_item", "supersedes", "TEXT NOT NULL DEFAULT ''"),
    ("memory_item", "superseded_by", "TEXT NOT NULL DEFAULT ''"),
    ("memory_item", "emotional_valence", 'REAL NOT NULL DEFAULT 0.0'),
    ("memory_item", "memory_type", "TEXT NOT NULL DEFAULT 'legacy_record'"),
    ("memory_item", "arousal", 'REAL NOT NULL DEFAULT 0.0'),
    ("memory_item", "evidence_weight", 'REAL NOT NULL DEFAULT 1.0'),

    ("memory_item", "vision_tiles", "TEXT DEFAULT NULL"),
    ("thought_node", "last_active_at", "TEXT"),
    ("thought_node", "in_residual", "INTEGER DEFAULT 0"),
    ("thought_node", "session_tag", "TEXT"),
    ("memory_item", "animation_meta", "TEXT DEFAULT NULL"),
    # v15 表情字典：用了几次、什么时候用、是什么类型
    ("sticker_cache", "usages", "INTEGER DEFAULT 0"),
    ("sticker_cache", "last_used_at", "TEXT DEFAULT ''"),
    ("sticker_cache", "contexts", "TEXT DEFAULT '[]'"),
    ("sticker_cache", "kind", "TEXT DEFAULT 'sticker'"),
)

_local = threading.local()
WRITE_LOCK = threading.RLock()


def set_db_path(path: str | Path) -> None:
    """测试与演示用：切换数据库文件（会关掉当前连接）。"""
    global DB_PATH
    close()
    DB_PATH = Path(path).resolve()


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn
    DB_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=20.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=20000")
    _local.conn = conn
    return conn


def close() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_columns(conn: sqlite3.Connection) -> list[str]:
    """给老库补新列（只加不删；已存在就跳过）。"""
    added: list[str] = []
    for table, column, ddl in _EXTRA_COLUMNS:
        try:
            if column in _columns(conn, table):
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            added.append(f"{table}.{column}")
        except sqlite3.OperationalError:
            continue
    return added


def init_db(force: bool = False) -> dict:
    """建库/迁移。幂等，可重复调用（只建缺的表、只补缺的列）。"""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with WRITE_LOCK:
        conn.executescript(sql)
        added = _ensure_columns(conn)
        required = ['confidence', 'valid_from', 'valid_until', 'supersedes', 'superseded_by', 'emotional_valence', 'memory_type', 'arousal', 'evidence_weight']
        missing = set(required) - _columns(conn, "memory_item")
        if missing:
            raise RuntimeError(f"Memory migration incomplete: {sorted(missing)}")
        from .repo import hybrid
        hybrid.initialize(conn)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migration(version, applied_at) VALUES(?, ?)",
            (SCHEMA_VERSION, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
    return {"db": str(DB_PATH), "version": SCHEMA_VERSION, "created": force, "added_columns": added}


def is_ready() -> bool:
    if not DB_PATH.exists():
        return False
    try:
        row = q1("SELECT value FROM meta WHERE key='schema_version'")
        return row is not None and int(row["value"]) >= SCHEMA_VERSION
    except Exception:
        return False


# ── 查询 ────────────────────────────────────────────────────────────────────
def q(sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return list(connect().execute(sql, tuple(args)).fetchall())


def q1(sql: str, args: Iterable[Any] = ()) -> sqlite3.Row | None:
    return connect().execute(sql, tuple(args)).fetchone()


def scalar(sql: str, args: Iterable[Any] = (), default: Any = None) -> Any:
    row = q1(sql, args)
    if row is None:
        return default
    return row[0]


def ex(sql: str, args: Iterable[Any] = ()) -> None:
    with WRITE_LOCK:
        conn = connect()
        conn.execute(sql, tuple(args))
        conn.commit()


def exmany(sql: str, rows: Iterable[Iterable[Any]]) -> int:
    with WRITE_LOCK:
        conn = connect()
        cur = conn.executemany(sql, [tuple(r) for r in rows])
        conn.commit()
        return cur.rowcount


@contextmanager
def write() -> Iterator[sqlite3.Connection]:
    """显式事务：多条写入要么全成，要么全回滚。"""
    with WRITE_LOCK:
        conn = connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise


# ── 元信息 / 备份 ───────────────────────────────────────────────────────────
def get_meta(key: str, default: Any = None) -> Any:
    row = q1("SELECT value FROM meta WHERE key=?", (key,))
    return row["value"] if row else default


def set_meta(key: str, value: Any) -> None:
    ex("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, str(value)))


def backup(keep: int = 7) -> str | None:
    """每日备份，保留最近 keep 份。"""
    if not DB_PATH.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / f"dafeiyu-{datetime.now():%Y%m%d-%H%M%S}.db"
    try:
        src = connect()
        dst = sqlite3.connect(str(target))
        with dst:
            src.backup(dst)
        dst.close()
    except Exception:
        try:
            shutil.copy2(DB_PATH, target)
        except Exception:
            return None
    files = sorted(BACKUP_DIR.glob("dafeiyu-*.db"))
    for old in files[:-keep]:
        try:
            old.unlink()
        except Exception:
            pass
    return str(target)


def stats() -> dict:
    """给界面/审计用的一览。"""
    out: dict[str, Any] = {"db": str(DB_PATH), "size_mb": 0.0}
    try:
        out["size_mb"] = round(DB_PATH.stat().st_size / 1024 / 1024, 2)
    except Exception:
        pass
    for table in (
        "memory_item", "thought_node", "learning_intent", "knowledge_item",
        "reward_event", "emotion_snapshot", "chitchat_log", "audit_event",
        "dialogue_message", "evidence",
    ):
        try:
            out[table] = scalar(f"SELECT COUNT(*) FROM {table}", default=0)
        except Exception:
            out[table] = 0
    return out

