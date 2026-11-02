# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""本机 HTTP 接口（127.0.0.1:18766）：给 QQ 桥接上报情绪事件用。

为什么要它：桥接现在自己算情绪并写 状态.json，两边会打架。
改成「桥接上报 → 核心算 → 桥接读快照」之后，情绪就只有一套算法（规格红线）。
桥接那边带一键回退：连不上就退回本地算。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import settings
from .emotion import detector_reward, detector_scale, policy as policy_mod, reward, vector
from .repo import system as sr

_server: ThreadingHTTPServer | None = None
_thread: threading.Thread | None = None


def _state_payload() -> dict:
    from .bridge import projection

    vec = vector.current()
    return projection.project_emotion_state(vec, reward.stats(), reward.last_ignore_ms(), dry_run=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "DaFeiYuCore/2.0"

    def finish(self) -> None:
        try:
            super().finish()
        finally:
            # Each HTTP request owns its thread-local connection.
            from . import db
            db.close()

    def log_message(self, fmt, *args):  # 静音：别把每条请求写进 stdout
        return

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8", "replace"))
        except Exception:
            return {}

    def do_GET(self):  # noqa: N802
        if self.path.startswith('/api/memory/editorial'):
            from .memory import editorial
            try:
                mid = parse_qs(urlparse(self.path).query).get('id', [''])[0]
                self._json(200, editorial.view(mid))
            except ValueError as exc:
                self._json(400, {'ok': False, 'error': str(exc)})
            return
        if self.path.startswith("/api/affinity"):
            from . import affinity
            day = parse_qs(urlparse(self.path).query).get("date", [""])[0]
            try:
                self._json(200, {"ok": True, **affinity.view(day)})
            except ValueError:
                self._json(400, {"ok": False, "error": "日期格式应为 YYYY-MM-DD"})
        elif self.path == "/api/learning/dashboard":
            from .learning import dashboard
            self._json(200, {"ok": True, **dashboard.view()})
        elif self.path == "/api/learning/job":
            from .learning import dashboard
            self._json(200, {"ok": True, "job": dashboard.job_status()})
        elif self.path.startswith("/api/maintenance/status"):
            from . import db
            from .cognition import scheduler
            self._json(200, {"ok": True, "schema_version": db.get_meta("schema_version"), **scheduler.status()})
        elif self.path.startswith("/api/retrieval/status"):
            from .repo import hybrid
            self._json(200, {"ok": True, **hybrid.status()})
        elif self.path.startswith("/api/affective/state"):
            from . import affective
            self._json(200, {"ok": True, **affective.status()})
        elif self.path.startswith("/api/emotion/state"):
            self._json(200, _state_payload())
        elif self.path.startswith("/api/emotion/dashboard"):
            from .emotion import dashboard
            day = parse_qs(urlparse(self.path).query).get("date", [""])[0]
            try:
                data = dashboard.view(day)
            except ValueError:
                self._json(400, {"ok": False, "error": "日期格式应为 YYYY-MM-DD"})
                return
            self._json(200, {"ok": True, **data})
        elif self.path.startswith("/api/thought/residual"):
            from .thought import residual as residual_mod
            from .thought import session as session_mod

            self._json(200, {"ok": True, "residual": residual_mod.status(vector.current()),
                             "sessions": session_mod.status()})
        elif self.path.startswith("/api/status"):
            from .cognition import chitchat
            from .refusal import arbiter as refusal_arbiter

            self._json(200, {"ok": True, "emotion": vector.status(),
                             "chitchat": chitchat.view(vector.current()),
                             "refusal": refusal_arbiter.default().status()})
        elif self.path.startswith("/api/curiosity/level"):
            # 给桥接用：现在有多"想学东西"（0~1）。抖音请示的冷却期例外就看它。
            level = 0.0
            detail = {}
            try:
                from . import curiosity_engine, homeostasis

                st = curiosity_engine.status()
                top = max((d.get("urgency", 0) for d in st.get("domains", [])), default=0.0)
                boredom, _ = homeostasis.boredom_signal()
                level = round(min(1.0, max(0.0, max(float(top), float(boredom)))), 3)
                detail = {"top_urgency": top, "boredom": boredom}
            except Exception as exc:  # noqa: BLE001
                detail = {"error": str(exc)}
            self._json(200, {"ok": True, "level": level, **detail})
        elif self.path.startswith("/api/hormones/state"):
            from . import hormones

            self._json(200, {"ok": True, **hormones.status(), "block": hormones.prompt_block()})
        elif self.path.startswith("/api/desk/state"):
            from . import deskstate

            self._json(200, {"ok": True, **deskstate.state(
                float(self.path.split("active=")[-1]) if "active=" in self.path else 5.0)})
        elif self.path.startswith("/api/douyin/state"):
            from . import douyin

            self._json(200, {"ok": True, **douyin.status()})
        elif self.path.startswith("/api/homeostasis/state"):
            from . import homeostasis

            self._json(200, {"ok": True, **homeostasis.status(),
                             "block": homeostasis.prompt_block()})
        elif self.path.startswith("/api/meme/status"):
            from . import memewiki

            self._json(200, {"ok": True, **memewiki.status()})
        elif self.path.startswith("/api/meme/list"):
            from . import memewiki

            self._json(200, {"ok": True, "rows": memewiki.all_learned()})
        else:
            self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):  # noqa: N802
        body = self._body()
        if self.path == '/api/memory/source-status':
            from .memory import chat_record
            events = body.get('events')
            if not isinstance(events, list) or len(events) > 80:
                self._json(400, {'ok': False, 'error': '请选择有效的消息来源'})
            else:
                self._json(200, chat_record.source_status(str(body.get('conversation') or '')[:160], events))
            return
        if self.path == '/api/memory/editorial':
            from .memory import editorial
            try:
                self._json(200, editorial.change(body))
            except ValueError as exc:
                self._json(400, {'ok': False, 'error': str(exc)})
            return
        if self.path in ("/api/learning/settings", "/api/learning/run"):
            from .learning import dashboard
            try:
                result = (dashboard.save_config(body) if self.path.endswith("/settings")
                          else dashboard.start_action(body))
                self._json(200, {"ok": True, **result})
            except (TypeError, ValueError) as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            except Exception as exc:
                self._json(500, {"ok": False, "error": str(exc)[:500]})
            return
        text = str(body.get("text") or "").strip()
        if self.path == "/api/chat/record":
            from .memory import chat_record
            try:
                self._json(200, chat_record.record(body))
            except ValueError as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            return
        if self.path == "/api/context/prepare":
            from .memory import context
            messages = body.get("messages") or []
            if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
                self._json(400, {"ok": False, "error": "messages 需要是消息列表"})
                return
            self._json(200, context.prepare(messages, conversation=str(body.get("conversation") or "unknown")[:160],
                                           include_memory=bool(body.get("include_memory", True)),
                                           allow_lookup=bool(body.get("allow_lookup", True))))
            return
        if self.path == "/api/memory/self-activities":
            from .memory import self_activity
            self._json(200, self_activity.recent())
            return
        if self.path == "/api/memory/recall":
            from .memory import retrieval
            out = retrieval.recall(str(body.get("query") or "")[:2000], vec=vector.current(),
                                   layers=["long1", "long2"], top_k=max(1, min(20, int(body.get("limit") or 5))),
                                   touch=bool(body.get("touch", True)), include_thoughts=False,
                                   include_history=bool(body.get("include_history", False)))
            self._json(200, {"ok": True, **out, "context": retrieval.context_block(out, 1600)})
            return
        if self.path == "/api/memory/remember":
            from .memory import service
            try:
                out = service.remember(str(body.get("content") or ""), category=str(body.get("category") or "平静"),
                                       subject=str(body.get("subject") or ""), conversation=str(body.get("conversation") or ""),
                                       event_id=str(body.get("event_id") or ""), self_activity=body.get("self_activity"))
                self._json(200, out)
            except ValueError as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            return
        if self.path == "/api/slang/lookup":
            from . import slang
            self._json(200, {"ok": True, **slang.lookup(str(body.get("term") or ""), allow_network=bool(body.get("allow_network", False)))})
            return
        if self.path.startswith("/api/emotion/event"):
            if not text:
                self._json(400, {"ok": False, "error": "text 不能为空"})
                return
            applied = []
            for hit in detector_reward.detect(text):
                ev = reward.apply_trigger(hit["trigger"], text, reason="桥接上报")
                if ev:
                    applied.append({"trigger": ev.trigger, "type": ev.type,
                                    "rpe": ev.rpe_weight, "delta": ev.delta})
            scale = detector_scale.apply(text)
            # v33：同一条消息顺手打一针激素（本地正则，0 调用），并让陪伴慢慢累积催产素
            try:
                from . import hormones

                pulse = hormones.observe(text, companion=True)
            except Exception:
                pulse = {}
            self._json(200, {"ok": True, "applied": applied, "scale": scale, "hormones": pulse,
                             "state": _state_payload()})
            return
        if self.path.startswith("/api/emotion/set"):
            values = body.get("values") or {}
            vector.set_values(values, reason="api")
            self._json(200, {"ok": True, "state": _state_payload()})
            return
        if self.path.startswith("/api/emotion/decay"):
            self._json(200, {"ok": True, "tick": vector.tick()})
            return
        if self.path.startswith("/api/chitchat"):
            from .cognition import chitchat

            out = chitchat.run(vec=vector.current(), manual=bool(body.get("manual", True)))
            self._json(200, {"ok": bool(out.get("ok")), "result": out})
            return
        if self.path.startswith("/api/dialogue"):
            from . import homeostasis
            from .cognition import dialogue

            try:
                homeostasis.note("user_message")
            except Exception:
                pass

            out = dialogue.handle_owner_message(text, conv=str(body.get("conv") or "private:owner"))
            self._json(200, out)
            return
        if self.path.startswith("/api/refusal/decide"):
            from .refusal import arbiter as refusal_arbiter

            if not text:
                self._json(400, {"ok": False, "error": "text 不能为空"})
                return
            decision = refusal_arbiter.default().decide(text, vector.current())
            self._json(200, {"ok": True, "refusal": decision.as_dict()})
            return
        if self.path.startswith("/api/refusal/react"):
            from .refusal import arbiter as refusal_arbiter

            decision_id = str(body.get("id") or "")
            reaction = str(body.get("reaction") or "").strip()
            if not decision_id or reaction not in ("accept", "insist", "ignore", "praise", "insult"):
                self._json(400, {"ok": False, "error": "id / reaction 不合法"})
                return
            row = refusal_arbiter.default().react(decision_id, reaction)
            self._json(200, {"ok": row is not None, "row": row})
            return
        if self.path.startswith("/api/sticker/lookup"):
            from . import sticker

            sha = str(body.get("sha256") or "")
            path = str(body.get("path") or "")
            if not sha and path:
                sha = sticker.sha256_file(path)
            data = sticker.lookup(sha)
            self._json(200, {
                "ok": True, "hit": data is not None, "sha256": sha,
                "summary": (data or {}).get("summary", ""),
                "text": sticker.describe(data), "note": (data or {}).get("note", ""),
                "usages": (data or {}).get("usages", 0),
                "contexts": (data or {}).get("contexts", []),
            })
            return
        if self.path.startswith("/api/sticker/remember"):
            from . import sticker

            sha = str(body.get("sha256") or "")
            ok = sticker.remember(sha, summary=str(body.get("summary") or ""),
                                  concepts=list(body.get("concepts") or []),
                                  note=str(body.get("note") or ""))
            self._json(200, {"ok": ok, "sha256": sha})
            return
        if self.path.startswith("/api/sticker/annotate"):
            from . import sticker

            sha = str(body.get("sha256") or body.get("path") and sticker.sha256_file(str(body.get("path"))) or "")
            ok = sticker.annotate(sha, str(body.get("note") or ""))
            self._json(200, {"ok": ok, "sha256": sha})
            return
        if self.path.startswith("/api/sticker/used"):
            from . import sticker

            sha = str(body.get("sha256") or "")
            ok = sticker.observe_usage(sha, str(body.get("context") or ""))
            self._json(200, {"ok": ok, "sha256": sha})
            return
        if self.path.startswith("/api/sticker/status"):
            from . import sticker

            self._json(200, {"ok": True, **sticker.status()})
            return
        if self.path.startswith("/api/thought/turn"):
            from .thought import residual as residual_mod
            from .thought import session as session_mod
            from .thought import turn as turn_mod

            conv = str(body.get("conv") or "private:owner")
            user_text = str(body.get("user_text") or "").strip()
            if not user_text:
                self._json(400, {"ok": False, "error": "user_text 不能为空"})
                return
            try:
                from . import homeostasis

                if not body.get("context_prepared"):
                    homeostasis.note("user_message")
            except Exception:
                pass
            out = turn_mod.record_turn(
                conv, event_id=str(body.get("event_id") or "")[:200], user_text=user_text, reply_text=str(body.get("reply_text") or ""),
                memory_ids=[str(i) for i in (body.get("memory_ids") or [])][:5],
                emotion_tag=str(body.get("emotion_tag") or "平静"),
                tone=str(body.get("tone") or "normal"),
                decision=str(body.get("decision") or ""),
            )
            cold = residual_mod.cold_start(conv, vec=vector.current())
            self._json(200, {"ok": True, "turn": out,
                             "cold_start": {"count": len(cold.get("nodes") or []),
                                            "block": cold.get("block", "")[:1500]}})
            return
        if self.path.startswith("/api/thought/residual"):
            from .thought import residual as residual_mod
            from .thought import session as session_mod

            self._json(200, {"ok": True, "residual": residual_mod.status(vector.current()),
                             "sessions": session_mod.status()})
            return
        if self.path.startswith("/api/thought/session"):
            from .thought import residual as residual_mod
            from .thought import session as session_mod
            from .thought import turn as turn_mod

            conv = str(body.get("conv") or "private:owner")
            action = str(body.get("action") or "status")
            if action == "end":
                out = session_mod.end(conv, reason="manual")
            elif action == "coldstart":
                out = residual_mod.cold_start(conv, vec=vector.current())
            elif action == "record":
                out = turn_mod.record_turn(conv, user_text=str(body.get("user_text") or "-"),
                                           reply_text=str(body.get("reply_text") or ""))
            else:
                out = {"current": session_mod.current(conv), "status": session_mod.status()}
            self._json(200, {"ok": True, "result": out})
            return
        if self.path.startswith("/api/vision/analyze"):
            from .vision import pipeline

            path = str(body.get("path") or "")
            if not path:
                self._json(400, {"ok": False, "error": "path 不能为空"})
                return
            out = pipeline.analyze(path, question=str(body.get("question") or ""),
                                   kind=str(body.get("kind") or "upload"),
                                   created_from="api")
            self._json(200, out)
            return
        if self.path.startswith("/api/douyin/start"):
            from . import douyin

            minutes = body.get("minutes")
            out = douyin.start(float(minutes) if minutes else None, log=lambda m: None)
            self._json(200, {"ok": bool(out.get("ok")), **out})
            return
        if self.path.startswith("/api/douyin/stop"):
            from . import douyin

            self._json(200, {"ok": True, **douyin.stop()})
            return
        if self.path.startswith("/api/homeostasis/event"):
            from . import homeostasis

            kind = str(body.get("kind") or "user_message")
            self._json(200, {"ok": True, **homeostasis.note(kind)})
            return
        if self.path.startswith("/api/meme/search"):
            from . import memewiki

            raw = str(body.get("text") or body.get("query") or "").strip()
            if not raw:
                self._json(400, {"ok": False, "error": "text 不能为空"})
                return
            query = memewiki.extract_query(raw)
            if not query:
                self._json(200, {"ok": True, "found": False, "query": "", "block": "",
                                 "reason": "看不出要查哪个词"})
                return
            record = bool(body.get("record", True))
            data = memewiki.search(query, record=record,
                                   context=str(body.get("context") or raw)[:80])
            if not data.get("found"):
                self._json(200, {"ok": True, "found": False, "query": query, "block": "",
                                 "reason": data.get("reason", "词库里没有")})
                return
            # 注意：search(record=True) 已经记过一次，这里不再记（否则一次查询算两次使用）
            self._json(200, {"ok": True, "found": True, "query": query,
                             "block": memewiki.block(query),
                             "title": data["title"], "match": data["match"],
                             "tags": data["tags"]})
            return
        if self.path.startswith("/api/meme/learn"):
            from . import memewiki

            title = str(body.get("title") or "").strip()
            if not title:
                self._json(400, {"ok": False, "error": "title 不能为空"})
                return
            ok = memewiki.learn(title, note=str(body.get("note") or ""),
                                context=str(body.get("context") or "")[:80])
            self._json(200, {"ok": ok, "title": title, "learned": memewiki.learned(title)})
            return
        self._json(404, {"ok": False, "error": "not found"})


def start(port: int | None = None) -> dict:
    global _server, _thread
    if _server is not None:
        return {"running": True, "port": _server.server_address[1]}
    port = int(port or settings.app().get("bridge", {}).get("core_emotion_api_port", 18766))
    _server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    _thread = threading.Thread(target=_server.serve_forever, name="dafeiyu-api", daemon=True)
    _thread.start()
    return {"running": True, "port": port}


def stop() -> None:
    global _server, _thread
    if _server is not None:
        try:
            _server.shutdown()
            _server.server_close()
        except Exception:
            pass
    _server = None
    _thread = None


def running() -> bool:
    return _server is not None
