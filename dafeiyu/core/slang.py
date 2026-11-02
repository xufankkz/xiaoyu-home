"""Unknown-term lookup: exact local dictionary -> local knowledge -> web UI.

Related words and semantic neighbours are evidence of relevance, never proof
that a complete term is known. Network work is explicit and uses the existing
normal browser login; this module has no paid API or vision client.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time

from . import memewiki
from .entity import KnowledgeItem
from .repo import hybrid, learning as lr, system as sr

_lock = threading.RLock()
_busy: set[str] = set()
_browser_lock = threading.Lock()
_failures: dict[str, tuple[float, dict]] = {}
_last_lookup: dict = {}


def status() -> dict:
    from . import settings
    with _lock:
        return {"active": len(_busy), "browser_profile_present": (settings.STATE / "browser-profile").is_dir(),
                **_last_lookup}


def _failed(term: str, result: dict) -> dict:
    global _last_lookup
    with _lock:
        _failures[term] = (time.monotonic() + 300, result)
        while len(_failures) > 128:
            _failures.pop(next(iter(_failures)))
        _last_lookup = {"last_status": result["status"], "last_reason": result.get("reason", "查询尚未完成")}
    return result


def local(term: str) -> dict:
    term = str(term or "").strip()
    if not re.fullmatch(r"[\w\u3400-\u9fff·-]{2,20}", term):
        return {"found": False, "term": term, "status": "invalid_term"}
    wiki = memewiki.search(term)
    if wiki.get("found") and wiki.get("exact"):
        return {"found": True, "term": term, "status": "local_dictionary", "definition": wiki.get("usage") or wiki.get("other") or wiki.get("origin"), "source": wiki.get("source"), "network_used": False}
    item = lr.knowledge_by_name(term)
    if item and item.domain == "slang" and item.confidence >= 0.65 and item.source:
        return {"found": True, "term": term, "status": "local_cache", "definition": item.content, "source": item.source, "id": item.id, "network_used": False}
    candidates = hybrid.search(term, "knowledge", 5)
    return {"found": False, "term": term, "status": "unknown", "network_used": False,
            "max_similarity": max((i["semantic"] for i in candidates["ranked"]), default=0.0),
            "reason": "未找到完整同名释义，单字或相近词条不能作为答案", "suggested_tool": "dafeiyu_lookup"}


def _parse_answer(answer: str, term: str) -> dict | None:
    text = str(answer or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("term") != term or data.get("uncertain") is not False:
        return None
    definition = str(data.get("definition") or "").strip()
    if len(definition) < 4 or len(definition) > 2000 or re.search(r"不确定|无法确认|不知道|未找到", definition):
        return None
    return {"definition": definition, "reference": str(data.get("source_url") or "")[:1000]}


def lookup(term: str, *, allow_network: bool = False, browser=None) -> dict:
    cached = local(term)
    if cached.get("found") or cached.get("status") != "unknown" or not allow_network:
        return cached
    with _lock:
        failure = _failures.get(term)
        if failure and failure[0] > time.monotonic():
            return {**failure[1], "network_used": False, "retry_later": True}
        if term in _busy:
            return {**cached, "status": "pending", "reason": "同一词条正在查询，不重复联网"}
        _busy.add(term)
    if not _browser_lock.acquire(blocking=False):
        with _lock:
            _busy.discard(term)
        return {**cached, "status": "pending", "reason": "浏览器正在完成另一个查询，稍后重试"}
    try:
        # Recheck after acquiring the per-term work slot.
        cached = local(term)
        if cached.get("found"):
            return cached
        if browser is None:
            from .webstudy import ask_web_deepseek
            browser = ask_web_deepseek
        question = (f"请查询网络用语「{term}」的含义与常见用法。不知道就标记 uncertain=true，不要猜测。"
                    '仅返回 JSON：{"term":"' + term + '","definition":"简短含义和用法",'
                    '"source_url":"可查证的出处（没有则留空）","uncertain":false}。')
        answer = browser(question, timeout_s=35)
        parsed = _parse_answer(answer, term)
        if parsed is None:
            return _failed(term, {**cached, "status": "unverified_answer", "network_used": True,
                    "reason": "网页回答不完整或不确定，未写入缓存；五分钟后可重试"})
        kid = "slang-" + hashlib.sha256(term.encode()).hexdigest()[:24]
        item = KnowledgeItem(id=kid, name=term, domain="slang", content=parsed["definition"],
                             concepts=[term], confidence=0.7, source="https://chat.deepseek.com/",
                             slots={"lookup": {"method": "existing-playwright-web-ui", "reference_claim": parsed["reference"], "verified_primary_source": False}})
        lr.knowledge_insert(item)
        hybrid.flush()
        # Populate both lanes now, so a repeated lookup needs neither web nor encoding.
        hybrid.search(term, "knowledge", 5)
        sr.audit_add("knowledge", kid, "slang_cached", {"term": term, "source": item.source, "cloud_api_calls": 0})
        global _last_lookup
        with _lock:
            _failures.pop(term, None)
            _last_lookup = {"last_status": "web_cached", "last_reason": "网页回答已缓存，原始出处尚未独立核实"}
        return {"found": True, "term": term, "status": "web_cached", "definition": item.content,
                "source": item.source, "reference_claim": parsed["reference"], "id": kid,
                "network_used": True, "verified_primary_source": False}
    except Exception as exc:
        from .webstudy import NeedLogin
        login = isinstance(exc, NeedLogin)
        return _failed(term, {**cached, "status": "login_required" if login else "lookup_failed",
                              "reason": "需要在正常浏览器中登录 DeepSeek 后再重试" if login else "网页查询失败，未保存词义；五分钟后可重试",
                              "network_used": True})
    finally:
        _browser_lock.release()
        with _lock:
            _busy.discard(term)
