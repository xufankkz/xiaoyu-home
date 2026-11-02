"""小鱼 · 日记服务（本地 HTTP，127.0.0.1:18889）

作用：把 `<workspace>/dafeiyu\\core\\diary.py` 的日记能力开放给「界面日历」和「本地 MCP」。
它自己不存数据：日记文件在 `<workspace>/dafeiyu\\data\\diary\\`，记忆索引在 dafeiyu.db 的
`memory_item`（layer=long2 / subtype=diary）。这里只做一层薄薄的 HTTP 壳 + 日志。

接口：
  GET  /health                                   → {ok, port, root}
  GET  /diary/status                             → 三类各几篇、最近一篇是哪天
  GET  /diary/list?kind=daily&year=2026&month=10 → 条目列表（不带正文）
  GET  /diary/get?kind=daily&date=2026-10-30     → 单篇全文（front matter + 正文）
  GET  /diary/calendar?kind=daily&year=2026&month=10 → {"days": {"2026-10-30": true, ...}}
  GET  /diary/memory_days?year=2026&month=10     → {"days": {...}} 记忆板块联动（哪天有记忆）
  GET  /diary/digest?days=7                      → 最近日记摘要文本（给聊天用）
  POST /diary/generate {kind,date,force}         → 触发生成，返回文件路径
  POST /diary/write    {kind,date,content,title,mood} → 手写一篇（默认同时写记忆索引）

用法：
  <workspace>/dafeiyu\\.venv\\Scripts\\python.exe <workspace>\\diary-service\\server.py
日志：
  <workspace>\\logs\\diary-service.log
"""

from __future__ import annotations
import release_paths

import json
import sys
import threading
import traceback
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"
PORT = release_paths.port('diary')
DAFEIYU = Path(release_paths.path('dafeiyu'))
LOG_FILE = Path(release_paths.path('logs/diary-service.log'))

if str(DAFEIYU) not in sys.path:
    sys.path.insert(0, str(DAFEIYU))

from core import diary  # noqa: E402  （sys.path 就位后才能导入）

_log_lock = threading.Lock()


def log(message: str) -> None:
    """日志写文件 + 打一份到 stdout（服务挂了至少还有文件）。"""
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    with _log_lock:
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_FILE, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass
    # 脱离控制台后台跑时 stdout 可能是 None / 已关闭，这里绝不能抛异常
    try:
        print(line, flush=True)
    except Exception:
        pass


def _int(value, default=None):
    try:
        return int(str(value).strip())
    except Exception:
        return default


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "XiaoyuDiary/1.0"

    # ── 底层 ────────────────────────────────────────────────────────────
    def _cors(self) -> None:
        """本机自用，放开跨源：Web UI 跑在别的端口，fetch 时不带这几个头会被浏览器拦掉。"""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_OPTIONS(self) -> None:  # noqa: N802
        """预检请求：直接放行，不读 body。"""
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._cors()
        self.end_headers()

    def _body(self) -> dict:
        length = _int(self.headers.get("Content-Length"), 0) or 0
        if length <= 0:
            return {}
        try:
            raw = self.rfile.read(length).decode("utf-8", "replace")
            data = json.loads(raw or "{}")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _query(self) -> dict:
        u = urllib.parse.urlparse(self.path)
        return {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}

    def log_message(self, fmt, *args) -> None:
        # 默认会往 stderr 每来一条打一行；改成进日志文件，格式自己定
        log(f"{self.command} {self.path} → {args[1] if len(args) > 1 else ''}")

    # ── GET ─────────────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        q = self._query()
        started = datetime.now()
        try:
            if u.path in ("/health", "/ping"):
                self._send(200, {"ok": True, "service": "diary", "port": PORT,
                                 "root": str(diary.DIARY_ROOT),
                                 "time": datetime.now().isoformat(timespec="seconds")})
            elif u.path == "/diary/status":
                self._send(200, {"ok": True, **diary.status()})
            elif u.path == "/diary/list":
                kind = diary.norm_kind(q.get("kind", "daily"))
                year = _int(q.get("year"))
                month = _int(q.get("month"))
                items = diary.list_entries(kind, year=year, month=month)
                self._send(200, {"ok": True, "kind": kind, "year": year, "month": month,
                                 "count": len(items), "items": items})
            elif u.path == "/diary/get":
                kind = diary.norm_kind(q.get("kind", "daily"))
                date = q.get("date") or None
                entry = diary.read_entry(kind, date)
                if entry is None:
                    self._send(404, {"ok": False, "error": "没有这一篇",
                                     "kind": kind, "date": date or ""})
                else:
                    self._send(200, {"ok": True, **entry})
            elif u.path == "/diary/calendar":
                kind = diary.norm_kind(q.get("kind", "daily"))
                now = datetime.now()
                year = _int(q.get("year"), now.year)
                month = _int(q.get("month"), now.month)
                if not year or not month or not (1 <= month <= 12):
                    self._send(400, {"ok": False, "error": "year/month 不对"})
                    return
                days = diary.calendar_days(year, month, kind)
                self._send(200, {"ok": True, "kind": kind, "year": year, "month": month,
                                 "count": sum(1 for v in days.values() if v), "days": days})
            elif u.path == "/diary/memory_days":
                now = datetime.now()
                year = _int(q.get("year"), now.year)
                month = _int(q.get("month"), now.month)
                if not year or not month or not (1 <= month <= 12):
                    self._send(400, {"ok": False, "error": "year/month 不对"})
                    return
                days = diary.memory_days(year, month)
                self._send(200, {"ok": True, "year": year, "month": month,
                                 "count": sum(1 for v in days.values() if v), "days": days})
            elif u.path == "/diary/digest":
                days = max(1, min(90, _int(q.get("days"), 7) or 7))
                text = diary.recent_diary_digest(days)
                self._send(200, {"ok": True, "days": days, "empty": not text, "text": text})
            else:
                self._send(404, {"ok": False, "error": "not found", "path": u.path})
        except ValueError as exc:
            self._send(400, {"ok": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            log(f"GET {self.path} 失败：{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            self._send(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        finally:
            cost = (datetime.now() - started).total_seconds()
            if cost > 3:
                log(f"GET {self.path} 用时 {cost:.1f}s")

    # ── POST ────────────────────────────────────────────────────────────
    def do_POST(self) -> None:  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        body = self._body()
        started = datetime.now()
        try:
            if u.path == "/diary/generate":
                kind = diary.norm_kind(body.get("kind") or "daily")
                date = body.get("date") or None
                force = bool(body.get("force"))
                if kind == "daily":
                    out = diary.generate_daily(date, force=force)
                elif kind == "weekly":
                    out = diary.generate_weekly(date, force=force)
                else:
                    out = diary.generate_monthly(date, force=force)
                cost = (datetime.now() - started).total_seconds()
                log(f"生成 {kind} {out.get('date')} → {out.get('source')}"
                    f"（{out.get('chars', '-')} 字，材料 {out.get('materials', '-')} 条，"
                    f"¥{out.get('llm_cost', '-')}，{cost:.1f}s）{out.get('path')}")
                self._send(200, out)
            elif u.path == "/diary/write":
                kind = diary.norm_kind(body.get("kind") or "daily")
                content = str(body.get("content") or "").strip()
                if not content:
                    self._send(400, {"ok": False, "error": "content 不能为空"})
                    return
                date = body.get("date") or None
                path = diary.write_entry(kind, date, content,
                                         title=(str(body.get("title") or "") or None),
                                         mood=(str(body.get("mood") or "") or None),
                                         source=str(body.get("source") or "manual"))
                out = {"ok": True, "kind": kind, "path": str(path), "source": "manual"}
                if body.get("link", True):
                    try:
                        out["memory"] = diary.link_memory(kind, date, content)
                    except Exception as exc:  # noqa: BLE001
                        out["memory"] = {"error": f"{type(exc).__name__}: {exc}"}
                log(f"手写 {kind} → {path}（{len(content)} 字）")
                self._send(200, out)
            else:
                self._send(404, {"ok": False, "error": "not found", "path": u.path})
        except ValueError as exc:
            self._send(400, {"ok": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            log(f"POST {self.path} 失败：{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            self._send(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})


def main() -> int:
    diary.ensure_dirs()
    log(f"diary-service 启动 http://{HOST}:{PORT}｜数据目录 {diary.DIARY_ROOT}")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("diary-service 收到中断，退出")
    finally:
        try:
            server.server_close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
