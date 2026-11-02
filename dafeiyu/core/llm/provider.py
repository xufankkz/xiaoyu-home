# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""LLM Provider 抽象：dsh（本机命令行，带联网）｜http（OpenAI 兼容）｜offline（模板兜底）。

规格要求「接口抽象成 Provider，支持离线降级为模板生成」。
原则：模型只当打字机用；写盘、阈值、冷却全部由 Python 判定，模型绕不过去。
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from .. import settings

PATCH_FILE = settings.CONFIG / "headless.patch.yml"
PATCH_BODY = """- id: agent-default-model
  name: '@deepseek-ai/dsh-agent-default-model'
  config:
    provider: deepseek-account
    model: deepseek-flash
    reasoningEffort: high
"""

USAGE_FILE = settings.LOGS / "llm_usage.jsonl"

# DeepSeek 官方价（元 / 百万 token，空闲时段）
PRICE = {"cache_hit": 0.02, "cache_miss": 1.0, "cache_write": 1.0, "output": 4.0}
PEAK_MULTIPLIER = 2.0
PEAK_WINDOWS = ((9, 12), (14, 18))


def _cfg() -> dict:
    return settings.llm_cfg()


def provider_for(task: str) -> str:
    cfg = _cfg()
    task_map = cfg.get("tasks") or {}
    return str(task_map.get(task) or cfg.get("default") or "dsh")


def ensure_patch() -> None:
    if not PATCH_FILE.exists():
        PATCH_FILE.parent.mkdir(parents=True, exist_ok=True)
        PATCH_FILE.write_text(PATCH_BODY, encoding="utf-8")


def available(task: str = "dialogue") -> bool:
    name = provider_for(task)
    cfg = _cfg().get("providers", {}).get(name, {})
    if name == "dsh":
        return Path(str(cfg.get("cmd") or "")).exists()
    if name == "http":
        return bool(cfg.get("base_url"))
    return True  # offline 永远可用（模板）


def is_peak(at: datetime | None = None) -> bool:
    dt = at or datetime.now()
    if dt.weekday() >= 5:
        return False
    return any(start <= dt.hour < end for start, end in PEAK_WINDOWS)


def estimate_cost(usage: dict, at: datetime | None = None) -> float:
    if not usage:
        return 0.0
    mult = PEAK_MULTIPLIER if is_peak(at) else 1.0
    cost = (
        usage.get("cache_miss", 0) * PRICE["cache_miss"]
        + usage.get("cache_hit", 0) * PRICE["cache_hit"]
        + usage.get("cache_write", 0) * PRICE["cache_write"]
        + usage.get("output", 0) * PRICE["output"]
    ) / 1_000_000
    return round(cost * mult, 6)


def _http_usage(usage: dict) -> dict:
    """把 OpenAI 风格的 usage 归一到本地计价结构（原版缺这个函数，所有 http 调用都会 NameError）。"""
    u = usage or {}
    prompt = int(u.get("prompt_tokens") or 0)
    completion = int(u.get("completion_tokens") or 0)
    cached = int(((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
    return {
        "cache_miss": max(0, prompt - cached),
        "cache_hit": cached,
        "cache_write": 0,
        "output": completion,
        "steps": 0,
        "turns": 1,
        "total": prompt + completion,
    }


def _parse_stream(raw: str) -> tuple[str, dict, bool]:
    parts: list[str] = []
    usage = {"cache_miss": 0, "cache_hit": 0, "cache_write": 0, "output": 0, "steps": 0, "turns": 0}
    saw = False
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        saw = True
        kind = ev.get("type")
        if kind == "text":
            parts.append(str(ev.get("text") or ""))
        elif kind == "status":
            phase = ev.get("phase")
            if phase == "step_end":
                u = ev.get("usage") or {}
                usage["cache_miss"] += int(u.get("inputTokens") or 0)
                usage["cache_hit"] += int(u.get("cacheReadTokens") or 0)
                usage["cache_write"] += int(u.get("cacheWriteTokens") or 0)
                usage["output"] += int(u.get("outputTokens") or 0)
                usage["steps"] += 1
            elif phase == "turn_start":
                usage["turns"] += 1
    return "".join(parts).strip(), usage, saw


def _record(task: str, usage: dict, ok: bool, chars: int, provider: str = "",
            feature_tag: str = "") -> dict:
    now = datetime.now()
    row = {
        "at": now.isoformat(timespec="seconds"), "task": task, "provider": provider or "",
        "ok": bool(ok), "chars": int(chars), "peak": is_peak(now), "usage": usage,
        "cost": estimate_cost(usage, now),
    }
    try:
        USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(USAGE_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # v5 费用归因：每次调用落一条 cost_event（懒导入，避免 provider ↔ cost 循环）
    try:
        from .. import cost as cost_mod

        tag = feature_tag or cost_mod.tag_of_task(task)
        usage_norm = {"cache_miss": usage.get("cache_miss", 0), "cache_hit": usage.get("cache_hit", 0),
                      "cache_write": usage.get("cache_write", 0), "output": usage.get("output", 0)} if usage else {}
        # 去重键：与 cost.import_ledgers 的 key 完全一致，避免同一次调用被记两次账
        cost_dedupe_key = f"engine|{row['at']}|{cost_mod._norm_task(task)}|{row['cost']}"
        cost_mod.record_event(tag, task=task, model=provider or "", usage=usage_norm,
                             estimated=row.get("_estimated", 0.0), actual=row["cost"],
                             source="engine", message_id=cost_dedupe_key,
                             meta={"peak": row["peak"], "ok": bool(ok)})
        if tag == "other" and str(task) not in ("refusal", "bridge-turn", ""):
            from ..repo import system as sr

            sr.audit_add("cost", task, "untagged_call", {"task": task, "feature_tag": tag})
    except Exception:
        pass
    return row


def _run_dsh(prompt: str, timeout: int, task: str = "dialogue",
             feature_tag: str = "") -> tuple[bool, str, dict]:
    cfg = _cfg().get("providers", {}).get("dsh", {})
    cmd_path = Path(str(cfg.get("cmd") or ""))
    if not cmd_path.exists():
        return False, "找不到 dsh 命令行（config/llm.json → providers.dsh.cmd）", {}
    ensure_patch()
    cmd = ["cmd", "/c", str(cmd_path), "--profile", "headless", "--patch", str(PATCH_FILE), "--json", "-"]
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, cwd=str(cfg.get("cwd") or settings.ROOT), env=env,
        )
    except subprocess.TimeoutExpired:
        return False, f"调用超时（{timeout}s）", _record(task, {}, False, 0, "dsh", feature_tag)
    except Exception as exc:  # noqa: BLE001
        return False, f"调用失败：{exc}", _record(task, {}, False, 0, "dsh", feature_tag)
    text, usage, saw = _parse_stream(proc.stdout or "")
    if not saw:
        text = (proc.stdout or "").strip()
    ok = proc.returncode == 0 and bool(text)
    meta = _record(task, usage, ok, len(text), "dsh", feature_tag)
    if not ok:
        tail = " / ".join((proc.stderr or "").strip().splitlines()[-3:]) or "（无输出）"
        return False, f"dsh 返回码 {proc.returncode}：{tail}", meta
    return True, text, meta


def _run_http(prompt: str, timeout: int, task: str = "dialogue",
              feature_tag: str = "") -> tuple[bool, str, dict]:
    import urllib.request

    cfg = _cfg().get("providers", {}).get("http", {})
    base = str(cfg.get("base_url") or "").rstrip("/")
    if not base:
        return False, "http provider 没配 base_url", {}
    key = str(cfg.get("api_key") or os.environ.get(str(cfg.get("api_key_env") or ""), ""))
    payload = {
        "model": cfg.get("model") or "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": float(cfg.get("temperature", 0.8)),
    }
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "User-Agent": "XiaoyuHome/0.2.0-beta.1",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        return bool(text), text, _record(task, _http_usage(data.get("usage") or {}), bool(text),
                                         len(text), "http", feature_tag)
    except Exception as exc:  # noqa: BLE001
        return False, f"http 调用失败：{exc}", _record(task, {}, False, 0, "http", feature_tag)


_ETHOS_CHECKED = False


def _guard_ethos(prompt: str) -> str:
    """把灵魂锚点注入到每一次调用的最顶层（不重复注入）。"""
    global _ETHOS_CHECKED
    try:
        import core_ethos

        if not _ETHOS_CHECKED:
            core_ethos.alarm_once()
            _ETHOS_CHECKED = True
        block = core_ethos.soul_block()
        if block and block not in prompt:
            return block + "\n\n" + prompt
    except Exception:
        pass
    return prompt


def run(prompt: str, *, task: str = "dialogue", timeout: int | None = None,
        feature_tag: str = "") -> tuple[bool, str, dict]:
    """统一入口：成功返回 (True, 正文, 用量)；失败返回 (False, 原因, {})。

    feature_tag 是 v5 的费用归因标签（chat/vision_static/vision_animation/…）；
    不传就按 task 自动映射，映射不到记 other 并写一条审计。
    """
    prompt = _guard_ethos(prompt)
    name = provider_for(task)
    cfg = _cfg().get("providers", {}).get(name, {})
    timeout = int(timeout or cfg.get("timeout_seconds") or settings.app().get("llm_timeout_seconds", 900))
    if name == "dsh":
        return _run_dsh(prompt, timeout, task, feature_tag)
    if name == "http":
        return _run_http(prompt, timeout, task, feature_tag)
    return False, "offline provider：按模板降级", {}


def last_usage(limit: int = 30) -> list[dict]:
    rows: list[dict] = []
    try:
        for line in USAGE_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    except Exception:
        return []
    return rows[-limit:]


def usage_summary() -> dict:
    rows = last_usage(400)
    today = datetime.now().strftime("%Y-%m-%d")
    today_rows = [r for r in rows if str(r.get("at", "")).startswith(today)]
    by_task: dict[str, dict] = {}
    for r in today_rows:
        slot = by_task.setdefault(str(r.get("task") or "?"), {"count": 0, "cost": 0.0})
        slot["count"] += 1
        slot["cost"] = round(slot["cost"] + float(r.get("cost") or 0), 6)
    return {
        "today_total": round(sum(float(r.get("cost") or 0) for r in today_rows), 6),
        "today_count": len(today_rows),
        "by_task": by_task,
        "recent": rows[-8:][::-1],
    }

