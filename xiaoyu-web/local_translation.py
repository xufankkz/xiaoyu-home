"""A persistent local-only Argos worker, isolated from the chat Python environment."""
from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import queue
import subprocess
import threading


class LocalTranslation:
    def __init__(self, root=None, *, timeout=120):
        self.root = Path(root or os.environ.get(
            "QQBOT_LOCAL_TRANSLATOR_ROOT", Path(__file__).resolve().parents[1] / "tools/argos-translate"))
        self.timeout = timeout
        self.lock = threading.Lock()
        self.process = None
        self.responses = None
        self.last_metrics = None
        atexit.register(self.close)

    @staticmethod
    def _read_responses(stream, responses):
        try:
            for line in stream:
                responses.put(line)
        except (OSError, ValueError):
            pass
        finally:
            responses.put(None)

    def _start(self):
        worker = self.root / 'translator/XiaoyuTranslator.exe'
        if not worker.is_file() or not (self.root / "packages").is_dir():
            raise RuntimeError("local translator is not installed")
        env = os.environ.copy()
        env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
                   QQBOT_LOCAL_TRANSLATOR_ROOT=str(self.root))
        self.process = subprocess.Popen(
            [str(worker)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.responses = queue.Queue()
        threading.Thread(target=self._read_responses,
                         args=(self.process.stdout, self.responses), daemon=True,
                         name="local-translation-output").start()

    def __call__(self, items):
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                self._close()
                self._start()
            try:
                self.process.stdin.write(json.dumps({"items": items}, ensure_ascii=False) + "\n")
                self.process.stdin.flush()
                line = self.responses.get(timeout=self.timeout)
                if line is None:
                    raise RuntimeError("local translator exited")
                response = json.loads(line)
                if response.get("error"):
                    raise RuntimeError("local translation failed: " + str(response["error"]))
                self.last_metrics = {"engine": response.get("engine"),
                                     "network_attempts": response.get("network_attempts")}
                return response["items"]
            except Exception:
                self._close()
                raise

    def _close(self):
        process, self.process = self.process, None
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()

    def close(self):
        with self.lock:
            self._close()


LOCAL_TRANSLATOR = LocalTranslation()
