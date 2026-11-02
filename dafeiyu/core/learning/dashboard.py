"""Shared learning controls; reads never seed domains or start learning jobs."""

from __future__ import annotations

import math
import shutil
import threading
from datetime import datetime
from uuid import uuid4

from .. import curiosity_engine, db, settings
from ..cognition import curiosity, metacognition
from ..repo import system as sr

_LOCK = threading.Lock()
_JOB: dict = {"running": False}


def job_status() -> dict:
    with _LOCK:
        return dict(_JOB)


def view() -> dict:
    schedule = settings.learning()
    flag = settings.read_state("learning_switch.json", {"enabled": True})
    return {
        "curiosity": curiosity.view(),
        "gaps": metacognition.gaps(12),
        "engine": curiosity_engine.status(seed=False),
        "schedule": {"enabled": bool(flag.get("enabled", True)),
                     "window_start": schedule["window_start"],
                     "window_end": schedule["window_end"],
                     "timezone": schedule["timezone"]},
        "job": job_status(),
    }


def _backup(path) -> None:
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))


def save_config(body: dict) -> dict:
    scope = body.get("scope")
    if scope == "schedule":
        if type(body.get("enabled")) is not bool:
            raise ValueError("定时学习开关需要是布尔值")
        value = settings.read_state("learning_switch.json", {})
        value["enabled"] = body["enabled"]
        path = settings.STATE / "learning_switch.json"
        _backup(path)
        settings.write_state("learning_switch.json", value)
        changed = {"enabled": value["enabled"]}
    elif scope == "engine":
        changed = {}
        for key in ("enabled", "auto_learn"):
            if key in body:
                if type(body[key]) is not bool:
                    raise ValueError(key + " 需要是布尔值")
                changed[key] = body[key]
        for key, lo, hi in (("interval_minutes", 5, 720), ("budget_limit_per_day", 0, 100)):
            if key in body:
                if type(body[key]) not in (int, float) or not math.isfinite(body[key]):
                    raise ValueError(key + " 需要是有效数字")
                value = body[key]
                if not lo <= value <= hi or (key == "interval_minutes" and value != int(value)):
                    raise ValueError(key + " 超出允许范围")
                changed[key] = int(value) if key == "interval_minutes" else float(value)
        if not changed:
            raise ValueError("没有要修改的字段")
        # Merge only the submitted controls; custom thresholds/weights remain intact.
        value = dict(curiosity_engine.cfg())
        value.update(changed)
        _backup(settings.FILES["curiosity_engine"])
        settings.save("curiosity_engine", value)
    else:
        raise ValueError("未知学习设置分区")
    sr.audit_add("learning", "shared-ui", "settings", {"scope": scope, "changed": changed})
    return {"saved": True, "scope": scope, "changed": changed}


def _work(job_id: str, action: str) -> None:
    try:
        if action == "engine":
            result = curiosity_engine.tick()
        else:
            from . import executor
            result = executor.run_one()
        state = {"result": result}
        sr.audit_add("learning", "shared-ui", "manual_" + action, {"job": job_id})
    except Exception as exc:  # Keep a readable result available after navigation/reload.
        state = {"error": str(exc)[:500]}
    finally:
        db.close()
    with _LOCK:
        _JOB.update(state, running=False, finished_at=datetime.now().isoformat(timespec="seconds"))


def start_action(body: dict) -> dict:
    action = body.get("action")
    if action not in ("learn", "engine"):
        raise ValueError("未知学习动作")
    with _LOCK:
        if _JOB.get("running"):
            return {"accepted": False, "job": dict(_JOB)}
        job_id = str(uuid4())
        _JOB.clear()
        _JOB.update(id=job_id, action=action, running=True,
                    started_at=datetime.now().isoformat(timespec="seconds"))
        worker = threading.Thread(target=_work, args=(job_id, action), name="learning-ui", daemon=True)
        try:
            worker.start()
        except Exception:
            _JOB.update(running=False, error="学习任务未能启动")
            raise
        return {"accepted": True, "job": dict(_JOB)}
