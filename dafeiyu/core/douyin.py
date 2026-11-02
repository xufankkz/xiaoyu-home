# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""刷抖音学梗（v37）：本地 Playwright + 真实 Edge，**0 API 花费**。

三条铁律（老大定的死命令）：
  1. 不用任何「原生抖音解析插件」（那套每刷一条都可能打一次 API）；
  2. 只抓**纯文本**（标题/评论/弹幕），绝不把截图或视频丢给模型「理解」；
  3. 拟人：每条停留 10s±2、滑动前 2~5s、贝塞尔移鼠标、15% 概率点个赞/展开评论。

谁在跑：核心进程里的一条后台线程（不是模型回合）。
  所以「刷 30 分钟」这件事**一个 token 都不花** —— 这也是为什么不让 QQ 那边调
  browser_* 工具：她每看一屏都要把整屏文本塞进上下文，10 条视频就是几十万 token。
  模型只在「要不要去刷」「刷完了要不要继续」这两句话上出现（走桥接的固定话术，0 token）。

数据落在 `learning/browse/<日期>.jsonl`，每条带 `feature_tag="local_browse"`，
花费账本里它就是 0 元（没有任何模型调用）。
"""

from __future__ import annotations

import json
import random
import re
import threading
import time
from datetime import datetime

from . import settings

DOUYIN_URL = "https://www.douyin.com/?recommend=1"
BROWSE_DIR = settings.LEARNING / "browse"
SHOT_DIR = settings.STATE / "shots"
PROFILE_DIR = settings.STATE / "browser-profile"
STATUS_FILE = "douyin.json"

DEFAULTS = {
    "enabled": True,
    "minutes": 30,
    "perVideoSeconds": 10,
    "jitterSeconds": 2,
    "preSlideSecondsMin": 2,
    "preSlideSecondsMax": 5,
    "likeProbability": 0.15,
    "maxEmptyStreak": 3,
    "headed": True,
}

# 验证码 / 安全验证：命中就**立刻停**，绝不硬刷（再刷就是给账号判死刑）。
# 注意别把登录弹窗里的「验证码登录」当成拦截 —— 那是登录方式，不是风控。
CAPTCHA_RE = re.compile(r"(滑块|拖动|安全验证|人机验证|请完成.{0,6}验证|异常流量|访问过于频繁|"
                        r"操作频繁|换个方式验证)")
# 登录墙：抖音未登录时会盖一层「登录后免费畅享高清视频」（扫码/验证码/密码登录）
LOGIN_RE = re.compile(r"(登录后免费|扫码登录|验证码登录|密码登录|请先登录|请登录后)")
NOISE_RE = re.compile(r"^(首页|推荐|关注|朋友|我的|搜索|登录|下载|打开|抖音|精选|直播|放映厅|"
                      r"赞|回复|分享|收藏|举报|评论|展开|收起|全屏|自动连播)$")
# 纯数字/点赞数的各种写法：1.2万、1.2w、12、1,234、10w+、3.2K
COUNT_RE = re.compile(r"^[\d.,，]+\s*(万|w|W|k|K|亿|\+)?$")


def cfg() -> dict:
    data = settings.read_json(settings.CONFIG / "douyin.json", {}) or {}
    out = dict(DEFAULTS)
    if isinstance(data, dict):
        out.update({k: v for k, v in data.items() if k in DEFAULTS})
    return out


# ── 纯函数（可单测，不需要浏览器）──────────────────────────────────────────
def captcha_hit(text: str) -> str:
    """页面文字里有没有验证拦截。返回命中的关键词（空串 = 没拦）。"""
    m = CAPTCHA_RE.search(str(text or ""))
    return m.group(0) if m else ""


def useful_lines(text: str, limit: int = 40) -> list[str]:
    """从整页文本里挑出「像评论/标题」的行：够长、不是导航词、去重保序。"""
    out, seen = [], set()
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if len(line) < 4 or len(line) > 120:
            continue
        if NOISE_RE.match(line) or COUNT_RE.match(line) or line in seen:
            continue
        seen.add(line)
        out.append(line)
        if len(out) >= limit:
            break
    return out


def is_empty_capture(lines: list[str]) -> bool:
    """这一屏算不算「什么都没抓到」——连续 3 次就该怀疑选择器/改版了。"""
    return len([line for line in (lines or []) if len(line) >= 6]) < 2


def summarize(lines: list[str], max_chars: int = 120) -> str:
    """一句话总结（不调模型）：取最长的两行。"""
    top = sorted((line for line in (lines or []) if len(line) >= 6), key=len, reverse=True)[:2]
    return " / ".join(top)[:max_chars]


# 二维码探测：找页面上「接近正方形、边长 120~320px」的 img/canvas，且图片真的加载完了。
# 不赌类名（抖音改版就挂），赌形状 + 加载状态 —— 登录弹窗里唯一符合这个形状的就是二维码。
QR_PROBE_JS = """
() => {
  const nodes = [...document.querySelectorAll('img, canvas')];
  for (const n of nodes) {
    const r = n.getBoundingClientRect();
    if (r.width < 120 || r.width > 320) continue;
    if (Math.abs(r.width - r.height) > 12) continue;
    if (r.bottom < 0 || r.top > window.innerHeight) continue;
    const cls = String(n.className || '') + ' ' + String(n.getAttribute('src') || '');
    if (/placeholder|loading|skeleton|default/i.test(cls)) continue;
    if (n.tagName === 'IMG' && (!n.complete || !n.naturalWidth)) continue;
    return true;
  }
  return false;
}
"""


def wait_qr(page, timeout_s: float = 9.0) -> bool:
    """等二维码画出来再截图（默认最多 9 秒，前 5 秒基本都在等它）。

    为什么必须等：抖音的登录弹窗先出白框，二维码是**后加载**的图片。
    框一出现就截图，发过去的是一张没有码的图 —— 老大扫不了（实测踩过一次）。
    """
    deadline = time.time() + max(1.0, float(timeout_s))
    while time.time() < deadline:
        try:
            if page.evaluate(QR_PROBE_JS):
                time.sleep(0.6)      # 让它把像素真正画完，别截到半张
                return True
        except Exception:
            pass
        try:
            page.wait_for_timeout(500)
        except Exception:
            time.sleep(0.5)
    return False


def _shot_path(tag: str) -> str:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    return str(SHOT_DIR / f"douyin-{tag}-{datetime.now():%Y%m%d-%H%M%S}.png")


# ── 运行状态 ────────────────────────────────────────────────────────────────
_lock = threading.RLock()
_run = {"running": False, "started_at": "", "videos": 0, "lines": 0, "minutes": 0.0,
        "stop": None, "last_error": "", "need": "", "shot": "", "finished_at": "",
        "summary": ""}


def _restore() -> None:
    """进程重启后把「上一次跑到哪」接回来：否则桥接会以为「没有结果」，重复请示。"""
    raw = settings.read_state(STATUS_FILE, {})
    if not isinstance(raw, dict):
        return
    with _lock:
        for key in ("started_at", "videos", "lines", "minutes", "last_error", "need", "shot",
                    "finished_at", "summary"):
            if key in raw and _run.get(key) in ("", 0, 0.0, None):
                _run[key] = raw[key]


_restore()


def status() -> dict:
    with _lock:
        out = {k: v for k, v in _run.items() if k != "stop"}
        out["config_minutes"] = cfg()["minutes"]
        return out


def _persist() -> None:
    with _lock:
        data = {k: v for k, v in _run.items() if k != "stop"}
    settings.write_state(STATUS_FILE, data)


def stop() -> dict:
    with _lock:
        if _run.get("stop") is not None:
            _run["stop"].set()
            return {"ok": True, "stopping": True}
    return {"ok": True, "running": False}


# ── 拟人化动作 ──────────────────────────────────────────────────────────────
def _bezier_move(page, x: float, y: float, steps: int = 12) -> None:
    """贝塞尔曲线移鼠标：起点取当前视口中心附近，控制点随机偏移。"""
    try:
        sx, sy = 640, 450
        cx, cy = (sx + x) / 2 + random.uniform(-120, 120), (sy + y) / 2 + random.uniform(-90, 90)
        for i in range(1, steps + 1):
            t = i / steps
            bx = (1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t ** 2 * x
            by = (1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t ** 2 * y
            page.mouse.move(bx, by)
            time.sleep(random.uniform(0.012, 0.045))
    except Exception:
        pass


def _maybe_interact(page) -> str:
    """15% 概率点一下（点赞区/评论按钮）。只点页面右侧固定区域，不点任何外链。"""
    if random.random() >= float(cfg()["likeProbability"]):
        return ""
    try:
        box = page.viewport_size or {"width": 1280, "height": 900}
        x = box["width"] - random.uniform(70, 120)
        y = box["height"] * random.uniform(0.35, 0.62)
        _bezier_move(page, x, y)
        page.mouse.click(x, y, delay=random.randint(40, 110))
        time.sleep(random.uniform(0.6, 1.6))
        return "like"
    except Exception:
        return ""


# ── 主循环 ──────────────────────────────────────────────────────────────────
def run(minutes: float | None = None, *, log=None) -> dict:
    """刷一轮。返回 {ok, videos, lines, minutes, need, shot, summary}。

    need 非空表示「卡住了，要人」：
      captcha   被验证拦截（截图已存，QQ 侧会发出去求救）
      selector  连续 N 条抓不到评论（大概率改版，问老大要不要更新选择器）
    """
    conf = cfg()
    total_minutes = float(minutes if minutes is not None else conf["minutes"])
    with _lock:
        if _run.get("running"):
            return {"ok": False, "error": "已经有一轮在跑"}
        _run.update({"running": True, "started_at": datetime.now().isoformat(timespec="seconds"),
                     "videos": 0, "lines": 0, "minutes": 0.0, "last_error": "", "need": "",
                     "shot": "", "finished_at": "", "summary": "", "stop": threading.Event()})
        stop_event = _run["stop"]
    _persist()

    def say(message: str) -> None:
        if callable(log):
            log(message)

    BROWSE_DIR.mkdir(parents=True, exist_ok=True)
    day_file = BROWSE_DIR / f"{datetime.now():%Y-%m-%d}.jsonl"
    end_at = time.time() + total_minutes * 60
    empty_streak = 0
    result = {"ok": True, "videos": 0, "lines": 0, "need": "", "shot": "", "summary": ""}
    seq = 0

    try:
        from playwright.sync_api import sync_playwright

        say(f"[douyin] 打开抖音（有头，0 API），计划 {total_minutes:.0f} 分钟")
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                channel="msedge",
                headless=not bool(conf["headed"]),
                viewport={"width": 1280, "height": 900},
                args=["--start-maximized"],
            )
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(DOUYIN_URL, timeout=90000, wait_until="domcontentloaded")
                page.wait_for_timeout(5000)
                body = page.inner_text("body")[:4000]
                # 先看登录墙（它里面也有「验证码登录」四个字，顺序反了就会误报成风控）
                if LOGIN_RE.search(body):
                    ready = wait_qr(page)
                    say(f"[douyin] 登录墙：{'二维码已出现，截图' if ready else '等了 9 秒没等到二维码，先截一张'}")
                    shot = _shot_path("login")
                    page.screenshot(path=shot)
                    result.update({"need": "login", "shot": shot, "qr": ready})
                    return result
                if captcha_hit(body):
                    shot = _shot_path("captcha")
                    page.screenshot(path=shot)
                    result.update({"need": "captcha", "shot": shot})
                    say(f"[douyin] 一进门就被验证拦了：{captcha_hit(body)}")
                    return result

                while time.time() < end_at and not stop_event.is_set():
                    seq += 1
                    text = page.inner_text("body")
                    hit = captcha_hit(text)
                    if hit:
                        shot = _shot_path("captcha")
                        try:
                            page.screenshot(path=shot)
                        except Exception:
                            shot = ""
                        result.update({"need": "captcha", "shot": shot})
                        say(f"[douyin] 被验证拦住（{hit}），已停并截图")
                        break

                    lines = useful_lines(text)
                    entry = {
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "seq": seq,
                        "feature_tag": "local_browse",
                        "lines": lines,
                        "summary": summarize(lines),
                        "api_cost": 0.0,
                    }
                    with day_file.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    result["videos"] = seq
                    result["lines"] += len(lines)
                    with _lock:
                        _run.update({"videos": seq, "lines": result["lines"],
                                     "minutes": round((time.time() - (end_at - total_minutes * 60)) / 60, 1)})

                    if is_empty_capture(lines):
                        empty_streak += 1
                        if empty_streak >= int(conf["maxEmptyStreak"]):
                            result.update({"need": "selector"})
                            say(f"[douyin] 连续 {empty_streak} 条抓不到内容，疑似改版")
                            break
                    else:
                        empty_streak = 0

                    # 拟人：停留 10s±2（期间也随时可以被打断）
                    dwell = float(conf["perVideoSeconds"]) + random.uniform(
                        -float(conf["jitterSeconds"]), float(conf["jitterSeconds"]))
                    if stop_event.wait(timeout=max(3.0, dwell)):
                        break
                    _maybe_interact(page)
                    time.sleep(random.uniform(float(conf["preSlideSecondsMin"]),
                                              float(conf["preSlideSecondsMax"])))
                    for key in ("ArrowDown", "Space"):
                        try:
                            page.keyboard.press(key)
                            break
                        except Exception:
                            continue
                    page.wait_for_timeout(800)
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
    except Exception as error:  # noqa: BLE001
        result.update({"ok": False, "last_error": str(error)})
        say(f"[douyin] 出错：{error}")
    finally:
        with _lock:
            _run.update({"running": False, "need": result.get("need", ""),
                         "shot": result.get("shot", ""),
                         "finished_at": datetime.now().isoformat(timespec="seconds"),
                         "videos": result.get("videos", 0), "lines": result.get("lines", 0)})
            result["summary"] = summarize([f"{result.get('videos', 0)} 条视频", f"{result.get('lines', 0)} 行文本"])
            _run["summary"] = result["summary"]
        _persist()
        result["minutes"] = round(total_minutes, 1)
    return result


def start(minutes: float | None = None, *, log=None) -> dict:
    """开一条后台线程刷（API/桥接都从这里进）。"""
    with _lock:
        if _run.get("running"):
            return {"ok": False, "error": "正在刷，别重复开"}
    thread = threading.Thread(target=run, kwargs={"minutes": minutes, "log": log},
                              name="dafeiyu-douyin", daemon=True)
    thread.start()
    return {"ok": True, "started": True, "minutes": minutes if minutes is not None else cfg()["minutes"]}
