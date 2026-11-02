# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""调度器：唯一叫醒认知循环的地方（APScheduler）。

规格 6.10 / 5.2 / v12 1.1：
- 每 20 秒（界面开）/ 60 秒（后台）：tick
- 每 10 分钟：衰减 + 蒸馏 + 情绪回归
- 学习窗口、自我对话、巩固、归档：**全部从 config/app.json → learning 读**，不写死
  默认：学习 00:00–01:00、自我对话 01:30–02:00、巩固 01:10、归档 04:00
- 每 2 分钟：外发队列重试
"""

from __future__ import annotations
import release_paths

import pathlib
import threading
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .. import db, settings
from ..bridge import push
from .. import homeostasis, hormones
from ..emotion import vector
from ..language import memory as langmem
from ..repo import system as sr
from . import chitchat, consolidate, curiosity, loop

_lock = threading.RLock()
_sched: BackgroundScheduler | None = None
LAST: dict[str, str] = {}


def _guard(name: str, fn) -> None:
    """隔离并记录单次失败；已发出的模型请求不自动重跑。"""
    try:
        out = fn()
        LAST[name] = datetime.now().isoformat(timespec="seconds")
        if isinstance(out, dict):
            sr.consolidation_add(f"job-{name}-{datetime.now():%Y%m%d%H%M%S}", name,
                                 {"ok": True, "summary": str(out)[:800]})
        return
    except Exception as exc:  # noqa: BLE001
        db.set_meta(f"job_error:{name}", f"{datetime.now():%Y-%m-%d %H:%M:%S} {exc}")
        # Requests may already have reached a paid endpoint. A job failure must
        # not silently invoke it again; the next scheduled job is a new decision.
        sr.consolidation_add(f"jobfail-{name}-{datetime.now():%Y%m%d%H%M%S}", name,
                             {"ok": False, "error": str(exc)})


# ── 各任务 ──────────────────────────────────────────────────────────────────
def job_tick() -> dict:
    return loop.run_once({"idle": True})


def job_periodic() -> dict:
    return consolidate.periodic()


def job_daily() -> dict:
    return consolidate.daily()


def job_learning() -> dict:
    from ..learning import executor

    if not bool(settings.read_state("learning_switch.json", {"enabled": True}).get("enabled", True)):
        return {"skipped": "定时学习开关已关闭"}
    today = datetime.now().strftime("%Y-%m-%d")
    if db.get_meta(f"learn_done:{today}") == "1":
        return {"skipped": "今天已经学过"}
    if not executor.window_open():
        return {"skipped": "不在学习窗口"}
    out = executor.scheduled_run(max_items=2)
    if out.get("ok"):
        db.set_meta(f"learn_done:{today}", "1")
    return out


def job_practice() -> dict:
    from ..learning import selfdialog

    today = datetime.now().strftime("%Y-%m-%d")
    if db.get_meta(f"practice_done:{today}") == "1":
        return {"skipped": "今天已经练过"}
    if not selfdialog.window_open():
        return {"skipped": "不在练习窗口"}
    out = selfdialog.run()
    if out.get("rounds"):
        db.set_meta(f"practice_done:{today}", "1")
    return out


def job_archive() -> dict:
    vector.snapshot("archive")
    return {"at": datetime.now().isoformat(timespec="seconds")}


def job_outbox() -> dict:
    return push.flush_outbox()


def job_cooldown() -> dict:
    """语言/情绪冷却到期 → 该问老大要不要更新了。"""
    out = {}
    for layer in ("language", "emotion"):
        out[layer] = langmem.ask(layer)
    return out


def job_chitchat() -> dict:
    state = chitchat.load()
    if not chitchat.due(state):
        return {"due": False}
    return chitchat.run(vec=vector.current())


def job_homeostasis() -> dict:
    """v31 内稳态心跳：每 60 秒一次，慢变量自己朝设定点回家。

    为什么单独一条 job 而不是塞进 tick：tick 是认知循环（20/60 秒，跟着界面模式走），
    内稳态要的是**稳定的一分钟**——界面关着、循环在忙、桥接没消息，它都得照算。
    """
    out = homeostasis.tick()
    if out.get("dominant"):
        db.set_meta("homeostasis:dominant", f"{out['dominant']}:{out['strength']}")
    return out


def job_hormones() -> dict:
    """v33 神经内分泌：每 10 分钟衰减一次、推基线漂移、让性格朝目标挪一小步。

    10 分钟而不是 60 秒：激素是分钟~天级的慢变量，算太勤只是白烧 CPU；
    而且 `tick()` 按 elapsed 分钟积分，隔多久跑都不会算错。
    """
    return hormones.tick()


def job_memory_maintenance() -> dict:
    from ..memory import reflection
    return reflection.maintain()


def job_memory_reflection() -> dict:
    from ..memory import reflection
    return reflection.run()


def job_memory_reflection_idle() -> dict:
    from .. import homeostasis
    from ..memory import reflection
    from datetime import datetime
    state = homeostasis.load()
    last = str(state.get("last_activity_at") or "")
    try:
        if (datetime.now() - datetime.fromisoformat(last)).total_seconds() < 1800:
            return {"skipped": "最近仍有互动"}
    except ValueError:
        return {"skipped": "没有可确认的空闲时间"}
    return reflection.run()


def job_affective() -> dict:
    from .. import affective
    return affective.tick()


def job_affinity() -> dict:
    from .. import affinity
    return affinity.summarize()


def job_fatigue() -> dict:
    return curiosity.tick_fatigue()


def job_curiosity_engine() -> dict:
    """v13：自主好奇心引擎。每 30 分钟扫一遍领域，挑一个最紧迫的学习。"""
    from .. import curiosity_engine

    out = curiosity_engine.tick()
    if out.get("target"):
        sr.audit_add("curiosity", out["target"], "autonomous_pick",
                     {"urgency": out.get("urgency"), "intent": out.get("intent")})
    return out


# ── 生命周期 ────────────────────────────────────────────────────────────────
def _tick_seconds() -> int:
    cfg = settings.app().get("tick", {})
    return int(cfg.get("app_seconds", 20) if mode_is_app() else cfg.get("headless_seconds", 60))


_APP_MODE = {"value": True}


def set_app_mode(flag: bool) -> None:
    _APP_MODE["value"] = bool(flag)


def mode_is_app() -> bool:
    return _APP_MODE["value"]


def _at(text: str) -> tuple[int, int]:
    hour, minute = str(text).split(":")
    return int(hour), int(minute)


# 这几类 job 的 id 前缀：启动时先按前缀清掉旧的，再按当前配置重挂（v12 1.1）
MANAGED_PREFIXES = (
    "session_rotate_", "webstudy_","learning_", "self_dialogue_", "daily_", "archive_", "curiosity_", "diary_")


def _clear_managed(sched: BackgroundScheduler) -> list[str]:
    removed = []
    try:
        for job in list(sched.get_jobs()):
            if any(str(job.id).startswith(p) for p in MANAGED_PREFIXES):
                try:
                    sched.remove_job(job.id)
                    removed.append(job.id)
                except Exception:
                    pass
    except Exception:
        pass
    return removed



def job_webstudy():
    """自主上网学习：每小时检查一次配额，到点就学一轮（内部有 120 分钟最小间隔）。"""
    try:
        from ..learning import webstudy
        return webstudy.tick()
    except Exception as exc:  # noqa: BLE001
        print(f"[scheduler] webstudy tick 失败：{exc}")
        return None


def job_session_rotate():
    """每天凌晨把 QQ 会话的 DSH 上下文重置一次（省钱，不影响长期记忆）。

    - 只在「最近 3 小时没说过话」的会话上做，避免打断夜聊
    - 重置后她仍有：大肥鱼长期记忆 + 桥接注入的最近消息快照，所以不会"忘人"
    - 用桥接控制台接口 /api/session/reset（需要 console-token）
    """
    import json as _json
    import time as _time
    import urllib.request as _url

    bridge = pathlib.Path(release_paths.path('qq-bridge'))
    try:
        tok = (bridge / "state" / "console-token").read_text(encoding="utf-8").strip()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"读不到 console-token: {exc}"}
    try:
        sessions = _json.loads((bridge / "state" / "sessions.json").read_text(encoding="utf-8")).get("sessions") or {}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"读不到 sessions.json: {exc}"}
    try:
        convs = (_json.loads((bridge / "state" / "social-v2.json").read_text(encoding="utf-8")).get("conversations") or {})
    except Exception:
        convs = {}

    now_ms = _time.time() * 1000
    done, skipped = [], []
    for key in list(sessions.keys()):
        last = float((convs.get(key) or {}).get("lastAiReplyAt") or 0)
        if last and now_ms - last < 3 * 3600 * 1000:
            skipped.append(key)
            continue
        try:
            req = _url.Request(
                release_paths.url('bridge', '/api/session/reset'),
                data=_json.dumps({"key": key}).encode(),
                headers={"Content-Type": "application/json", "x-console-token": tok},
                method="POST",
            )
            with _url.urlopen(req, timeout=20) as r:
                ok = _json.loads(r.read().decode("utf-8", "replace")).get("ok", False)
            (done if ok else skipped).append(key)
        except Exception:
            skipped.append(key)
    print(f"[scheduler] 会话轮换：重置 {len(done)} 个，跳过 {len(skipped)} 个 {done}")
    return {"ok": True, "reset": done, "skipped": skipped}


def start() -> BackgroundScheduler:
    global _sched
    with _lock:
        if _sched is not None:
            return _sched
        win = settings.learning()
        from .. import affinity
        affinity.initialize()
        sched = BackgroundScheduler(timezone=win.get("timezone") or "Asia/Shanghai")
        _clear_managed(sched)

        sched.add_job(lambda: _guard("affinity_daily", job_affinity),
                      CronTrigger(hour=0, minute=10, timezone="Asia/Shanghai"),
                      id="affinity_daily", max_instances=1, coalesce=True, misfire_grace_time=3600)

        sched.add_job(lambda: _guard("memory_maintenance", job_memory_maintenance), IntervalTrigger(minutes=5),
                      id="memory_maintenance", max_instances=1, coalesce=True)
        sched.add_job(lambda: _guard("memory_reflection", job_memory_reflection), CronTrigger(hour=3, minute=30, timezone=sched.timezone),
                      id="memory_reflection", max_instances=1, coalesce=True)
        sched.add_job(lambda: _guard("memory_reflection_idle", job_memory_reflection_idle), IntervalTrigger(minutes=30),
                      id="memory_reflection_idle", max_instances=1, coalesce=True)
        sched.add_job(lambda: _guard("affective", job_affective), IntervalTrigger(minutes=1),
                      id="affective_state", max_instances=1, coalesce=True)

        sched.add_job(lambda: _guard("session_rotate", job_session_rotate), CronTrigger(hour=4, minute=30),
                      id="session_rotate", max_instances=1, coalesce=True)
        sched.add_job(lambda: _guard("webstudy", job_webstudy), IntervalTrigger(minutes=60),
                      id="webstudy_tick", max_instances=1, coalesce=True, jitter=120)
        sched.add_job(lambda: _guard("tick", job_tick), IntervalTrigger(seconds=_tick_seconds()),
                      id="tick", max_instances=1, coalesce=True)
        sched.add_job(lambda: _guard("periodic", job_periodic), IntervalTrigger(minutes=10),
                      id="periodic", max_instances=1, coalesce=True)
        sched.add_job(lambda: _guard("chitchat", job_chitchat), IntervalTrigger(minutes=1),
                      id="chitchat", max_instances=1, coalesce=True)
        # v31 内稳态：每分钟一次（规格要求 60 秒），补跑也不会算错（按 elapsed 算）
        sched.add_job(lambda: _guard("homeostasis", job_homeostasis), IntervalTrigger(seconds=60),
                      id="homeostasis", max_instances=1, coalesce=True)
        # v33 激素：10 分钟一次（衰减 + 基线漂移 + 性格演化）
        sched.add_job(lambda: _guard("hormones", job_hormones), IntervalTrigger(minutes=10),
                      id="hormones", max_instances=1, coalesce=True)
        sched.add_job(lambda: _guard("outbox", job_outbox), IntervalTrigger(minutes=2),
                      id="outbox", max_instances=1, coalesce=True)
        sched.add_job(lambda: _guard("cooldown", job_cooldown), CronTrigger(minute=5),
                      id="cooldown", max_instances=1, coalesce=True)
        sched.add_job(lambda: _guard("fatigue", job_fatigue), CronTrigger(hour="*/6", minute=10),
                      id="fatigue", max_instances=1, coalesce=True)

        # ── 学习相关：时间全部来自 config/app.json → learning ──
        sched.add_job(lambda: _guard("learning", job_learning),
                      CronTrigger(hour=_at(win["window_start"])[0], minute=_at(win["window_start"])[1]),
                      id="learning_run", max_instances=1, coalesce=True)
        sched.add_job(lambda: _guard("learning_mid", job_learning),
                      CronTrigger(hour=_at(win["window_end"])[0], minute=_at(win["window_end"])[1]),
                      id="learning_mid", max_instances=1, coalesce=True)
        sched.add_job(lambda: _guard("practice", job_practice),
                      CronTrigger(hour=_at(win["self_dialogue_start"])[0],
                                  minute=_at(win["self_dialogue_start"])[1]),
                      id="self_dialogue_run", max_instances=1, coalesce=True)
        sched.add_job(lambda: _guard("daily", job_daily),
                      CronTrigger(hour=_at(win["consolidate_at"])[0],
                                  minute=_at(win["consolidate_at"])[1]),
                      id="daily_consolidate", max_instances=1, coalesce=True)
        sched.add_job(lambda: _guard("archive", job_archive),
                      CronTrigger(hour=_at(win["archive_at"])[0],
                                  minute=_at(win["archive_at"])[1]),
                      id="archive_daily", max_instances=1, coalesce=True)
        # v13 好奇心引擎：间隔可配（默认 30 分钟），开关在 config/curiosity_engine.json
        try:
            interval = int(settings.load("curiosity_engine").get("interval_minutes", 30) or 30)
        except Exception:
            interval = 30
        sched.add_job(lambda: _guard("curiosity_engine", job_curiosity_engine),
                      IntervalTrigger(minutes=max(1, interval)),
                      id="curiosity_engine_tick", max_instances=1, coalesce=True, jitter=30)
        # ── 日记 / 周记 / 月记：写盘 + 回写一条 long2 记忆索引 ──
        # 日 23:40（当天最后一件事）｜周 周日 23:50（收尾本周 ISO 周）｜月 1 号 23:55（收上个月）
        sched.add_job(lambda: _guard("diary_daily", job_diary_daily),
                      CronTrigger(hour=23, minute=40),
                      id="diary_daily", max_instances=1, coalesce=True)
        sched.add_job(lambda: _guard("diary_weekly", job_diary_weekly),
                      CronTrigger(day_of_week="sun", hour=23, minute=50),
                      id="diary_weekly", max_instances=1, coalesce=True)
        sched.add_job(lambda: _guard("diary_monthly", job_diary_monthly),
                      CronTrigger(day=1, hour=23, minute=55),
                      id="diary_monthly", max_instances=1, coalesce=True)
        sched.start()
        _sched = sched
        # 补跑放后台：开局就跑 daily 会卡住界面（要压缩、要调模型）
        threading.Thread(target=_catch_up, name="dafeiyu-catchup", daemon=True).start()
        return sched


def stop() -> None:
    global _sched
    with _lock:
        if _sched is not None:
            try:
                _sched.shutdown(wait=False)
            except Exception:
                pass
            _sched = None


def _catch_up() -> None:
    """开机补跑：窗口过了就算了，但要记一笔，别静默漏掉。"""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    _guard("affinity_daily", job_affinity)
    try:
        if now.hour >= 3 and db.get_meta(f"daily_done:{today}") != "1":
            _guard("daily_light", lambda: consolidate.daily(light=True))
            db.set_meta(f"daily_done:{today}", "1")
    except Exception:
        pass


def jobs() -> list[dict]:
    if _sched is None:
        return []
    out = []
    for job in _sched.get_jobs():
        out.append({"id": job.id, "next": str(getattr(job, "next_run_time", "")),
                    "trigger": str(job.trigger)})
    return out


def status() -> dict:
    return {"running": _sched is not None, "jobs": jobs(), "last": dict(LAST),
            "tick_seconds": _tick_seconds()}


# ── 日记 / 周记 / 月记（v34 追加；素材来自记忆与对话，写 md + 回写 long2 索引）────────
# 三个 job 都自己吞异常：日记写不出来是小事，绝不能把调度拖垮。
# 失败时返回 dict（而非抛出去），避免 _guard 的重试把模型调用再打一遍。
def job_diary_daily() -> dict:
    """每天 23:40：写今天的日记。"""
    try:
        from .. import diary

        return diary.generate_daily()
    except Exception as exc:  # noqa: BLE001
        print(f"[scheduler] 日记生成失败：{exc}")
        return {"ok": False, "kind": "daily", "error": str(exc)[:300]}


def job_diary_weekly() -> dict:
    """每周日 23:50：写本周（ISO 周）的周记。"""
    try:
        from .. import diary

        return diary.generate_weekly()
    except Exception as exc:  # noqa: BLE001
        print(f"[scheduler] 周记生成失败：{exc}")
        return {"ok": False, "kind": "weekly", "error": str(exc)[:300]}


def job_diary_monthly() -> dict:
    """每月 1 号 23:55：写**上个月**的月记（generate_monthly 默认就是上个月）。"""
    try:
        from .. import diary

        return diary.generate_monthly()
    except Exception as exc:  # noqa: BLE001
        print(f"[scheduler] 月记生成失败：{exc}")
        return {"ok": False, "kind": "monthly", "error": str(exc)[:300]}
