"""Chinese display translations; source logs stay untouched and only viewed pages are queued."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import queue
import re
import threading
import time


def needs_translation(text):
    words = re.findall(r"[A-Za-z]+(?:['’-][A-Za-z]+)*", str(text or ""))
    letters = sum(len(word) for word in words)
    return (len(words) >= 3 and letters >= 8) or (
        letters >= 4 and not re.search(r"[\u3400-\u9fff]", str(text or "")))


class PartialTranslationError(ValueError):
    def __init__(self, translations):
        super().__init__("some local translations failed validation")
        self.translations = translations


def translate_batch(items, fish_root=None):
    """Translate locally. This path never imports or falls back to an HTTP model."""
    from local_translation import LOCAL_TRANSLATOR

    result = LOCAL_TRANSLATOR(items)
    expected = {item["id"] for item in items}
    if not isinstance(result, dict) or not set(result).issubset(expected):
        raise ValueError("translation response shape")
    mapped = {}
    for key, value in result.items():
        translated = str(value or "").strip()
        if translated and len(translated) <= 60000 and re.search(r"[\u3400-\u9fff]", translated):
            mapped[key] = translated
    if set(mapped) != expected:
        raise PartialTranslationError(mapped)
    return mapped


class ThoughtTranslations:
    def __init__(self, cache_path, translate, *, retry_seconds=120, max_attempts=3):
        self.path = Path(cache_path)
        self.translate = translate
        self.retry_seconds = retry_seconds
        self.max_attempts = max_attempts
        self.lock = threading.RLock()
        self.cache = {}
        self.pending = set()
        self.failed = {}
        self.attempts = {}
        self.failure_kinds = {}
        self.blocked_until = 0
        self.consecutive_failures = 0
        self.jobs = queue.Queue(maxsize=256)
        self.worker = None
        self.revision = 0
        self.stopped = False
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            if stored.get("version") == 1:
                for key, text in list(stored.get("translations", {}).items())[-5000:]:
                    if (re.fullmatch(r"[a-f0-9]{64}", key) and isinstance(text, str)
                            and re.search(r"[\u3400-\u9fff]", text) and len(text) <= 60000):
                        self.cache[key] = text
                for key, failure in list(stored.get("failures", {}).items())[-5000:]:
                    if re.fullmatch(r"[a-f0-9]{64}", key) and isinstance(failure, dict):
                        self.attempts[key] = max(0, min(int(failure["attempts"]), max_attempts))
                        self.failed[key] = float(failure["retry_after"])
                        self.failure_kinds[key] = "validation" if failure.get("kind") == "validation" else "engine"
                self.blocked_until = float(stored.get("blocked_until", 0))
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            pass

    @staticmethod
    def key(text):
        return hashlib.sha256(("zh-v1\n" + text).encode("utf-8")).hexdigest()

    def project(self, items):
        shown = []
        with self.lock:
            for item in items:
                text = str(item.get("text") or "")
                if not needs_translation(text):
                    shown.append(item)
                    continue
                key = self.key(text)
                if key in self.cache:
                    shown.append(dict(item, text=self.cache[key], translation="ready"))
                    continue
                exhausted = self.attempts.get(key, 0) >= self.max_attempts
                failed = exhausted or max(self.failed.get(key, 0), self.blocked_until) > time.time()
                if not failed and key not in self.pending and len(text) <= 24000 and not self.stopped:
                    try:
                        self.jobs.put_nowait((key, text))
                        self.pending.add(key)
                    except queue.Full:
                        pass
                status = "stopped" if exhausted or len(text) > 24000 else "failed" if failed else "pending"
                if status == "stopped":
                    message = "这段记录的本地翻译已停止自动重试，原始记录仍保留。"
                elif status == "failed" and self.failure_kinds.get(key) == "validation":
                    message = "这段记录的本地译文未通过文字校验，稍后重试；原始记录仍保留。"
                elif status == "failed":
                    message = "本地翻译暂时不可用，稍后重试；不会调用翻译 API。"
                else:
                    message = "这段思考正在本机转成中文…"
                shown.append(dict(item, text=message, translation=status))
            if self.pending and (self.worker is None or not self.worker.is_alive()):
                self.worker = threading.Thread(target=self._work, name="thought-translation", daemon=True)
                self.worker.start()
        return shown

    def _save(self):
        with self.lock:
            while len(self.cache) > 5000:
                self.cache.pop(next(iter(self.cache)))
            data = {"version": 1, "translations": dict(self.cache),
                    "failures": {key: {"attempts": self.attempts[key], "retry_after": self.failed.get(key, 0),
                                       "kind": self.failure_kinds.get(key, "engine")}
                                 for key in list(self.attempts)[-5000:]},
                    "blocked_until": self.blocked_until}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)

    def _work(self):
        while not self.stopped:
            try:
                first = self.jobs.get(timeout=.2)
            except queue.Empty:
                continue
            with self.lock:
                if self.blocked_until > time.time():
                    self.pending.discard(first[0])
                    self.jobs.task_done()
                    continue
            batch = [first]
            chars = len(first[1])
            while len(batch) < 6 and chars < 8000:
                try:
                    extra = self.jobs.get_nowait()
                except queue.Empty:
                    break
                batch.append(extra)
                chars += len(extra[1])
            try:
                result = self.translate([{"id": key, "text": text} for key, text in batch])
                if set(result) != {key for key, _ in batch}:
                    raise ValueError("translation batch incomplete")
                with self.lock:
                    self.cache.update(result)
                    for key, _ in batch:
                        self.failed.pop(key, None)
                        self.attempts.pop(key, None)
                        self.failure_kinds.pop(key, None)
                    self.consecutive_failures = 0
                    self.blocked_until = 0
                self._save()
            except Exception as exc:
                good = exc.translations if isinstance(exc, PartialTranslationError) else {}
                with self.lock:
                    self.cache.update(good)
                    if isinstance(exc, PartialTranslationError):
                        self.consecutive_failures = 0
                        self.blocked_until = 0
                    else:
                        self.consecutive_failures += 1
                        self.blocked_until = time.time() + self.retry_seconds * 2 ** min(self.consecutive_failures - 1, 5)
                    for key, _ in batch:
                        if key in good:
                            self.attempts.pop(key, None)
                            self.failed.pop(key, None)
                            self.failure_kinds.pop(key, None)
                            continue
                        self.attempts[key] = self.attempts.get(key, 0) + 1
                        self.failure_kinds[key] = "validation" if isinstance(exc, PartialTranslationError) else "engine"
                        self.failed[key] = time.time() + self.retry_seconds * 2 ** min(self.attempts[key] - 1, 5)
                try:
                    self._save()
                except OSError:
                    pass
            finally:
                with self.lock:
                    for key, _ in batch:
                        self.pending.discard(key)
                    self.revision += 1
                for _ in batch:
                    self.jobs.task_done()

    def close(self):
        self.stopped = True
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=1)
