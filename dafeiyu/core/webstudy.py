# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""刷抖音学梗 / 问网页版 DeepSeek：**全程 0 API 花费**（v24 重做版）。

为什么要有这一层：
  `dsh-douyin-browser-local` 那套插件自带模型调用（spark / 自主评论 / 观察判定）——
  每刷一条都可能打一次 API，钱是从「刷抖音」这个动作里流出去的，老大明确不要这部分费用。
  这里的做法是：**本机真实 Edge（有头、可见、可接管）**负责「看」，
  **网页版 chat.deepseek.com** 负责「理解」（吃的是网页免费额度，不走 API），
  学到的东西写进本地梗词库 —— 以后她聊天时查这个词，依然是 0 花费。

链路：
    打开抖音（Edge）  → 每 10s±2 抓一次可见文字（标题/评论）
    → 攒够一批 → 粘进网页版 DeepSeek 问「这些都是什么梗」
    → 解析成 {词, 怎么用, 出处} → learning/memewiki/terms.json
    → 桥接预查命中 → 只花她正常聊天那点 token

三条硬规矩（和补丁一致）：
  1. 浏览器一律**有头**（headless=False）：老大能看见、能随时接管；
  2. 拟人节奏：每条视频停留 10s±2，切下一条前再随机 2~5s，别暴力刷；
  3. 不碰任何 API key —— 这个模块根本不 import llm 客户端。

── v36 新增：**自主上网学习**（她没事的时候自己找东西学）──────────────────
上面那条是「刷抖音学梗」；下面这条是「自由上网学习」，两者共用本文件但不互相干扰：
    core/learning/webstudy.py   闭环本体（选题 → 抓取 → 负面过滤 → 总结 → 写知识库 → 报告）
    core/learning/webfilter.py  负面内容屏蔽（纯函数 block_negative，带正反例自测）
本文件只做「对外入口 + 老 API 兼容」：面板/桥接仍然 import core.webstudy，
命令行自检也在这里（`python -m core.webstudy --dry-run`）。

自主上网的三条硬约束：负面内容命中即丢（不送模型、不写记忆）、站点白名单 + 每日配额、
无头浏览不弹窗。开关与词表都在 data/config/webstudy.json。
"""

from __future__ import annotations

import json
import random
import re
import time
from datetime import datetime
from pathlib import Path

from . import settings

DOUYIN_URL = "https://www.douyin.com/?recommend=1"
DEEPSEEK_URL = "https://chat.deepseek.com/"
TERMS_FILE = settings.LEARNING / "memewiki" / "terms.json"
PROFILE_DIR = settings.STATE / "browser-profile"


class NeedLogin(RuntimeError):
    """浏览器里没有登录态：需要老大在弹出的窗口里登录一次（登录态会留在本地 profile）。"""


class Stopped(RuntimeError):
    pass


def profile_dir() -> Path:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return PROFILE_DIR


def _log(log, message: str) -> None:
    if callable(log):
        log(message)


# ── 词库：学到的梗 ──────────────────────────────────────────────────────────
def load_terms() -> list[dict]:
    try:
        data = json.loads(TERMS_FILE.read_text(encoding="utf-8"))
        return [row for row in data if isinstance(row, dict) and row.get("title")] if isinstance(data, list) else []
    except Exception:
        return []


def save_terms(rows: list[dict]) -> None:
    seen = {}
    for row in load_terms() + list(rows or []):
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        old = seen.get(title, {})
        merged = {**old, **{k: v for k, v in row.items() if v not in (None, "")}}
        merged["title"] = title
        merged["updated_at"] = str(row.get("updated_at") or old.get("updated_at")
                                   or datetime.now().isoformat(timespec="seconds"))
        seen[title] = merged
    out = sorted(seen.values(), key=lambda r: str(r.get("updated_at") or ""), reverse=True)[:500]
    settings.write_text_atomic(TERMS_FILE, json.dumps(out, ensure_ascii=False, indent=2) + "\n")


_PAIR_RE = re.compile(r"^\s*[-*]?\s*(?P<title>[^|｜]{1,20})\s*[|｜]\s*(?P<usage>.+?)\s*$")


def parse_meme_lines(text: str) -> list[dict]:
    """把网页版 DeepSeek 的「词｜怎么用」清单解析成条目。

    只认这一种格式（模型偶尔会多写解释，认不出来就丢掉，不猜）。
    """
    rows = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _PAIR_RE.match(line)
        if not m:
            continue
        title = m.group("title").strip(" -*：:「」\"'")
        usage = m.group("usage").strip()
        if not title or len(title) > 20 or not usage:
            continue
        if title in {"词", "词语", "标题", "梗"}:
            continue
        rows.append({"title": title, "usage": usage[:600], "source": "抖音+网页版DeepSeek",
                     "updated_at": datetime.now().isoformat(timespec="seconds")})
    return rows


# ── 浏览器 ──────────────────────────────────────────────────────────────────
def _launch(pw):
    # 有头 + 持久 profile：登录一次就不用再登，且老大随时能接手操作
    return pw.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir()),
        channel="msedge",
        headless=False,
        viewport={"width": 1280, "height": 900},
        args=["--start-maximized"],
    )


def _first_send_target(page):
    """找输入框：DeepSeek 是 textarea，个别版本是 contenteditable。"""
    for sel in ("textarea", "div[contenteditable='true']", "[role='textbox']"):
        try:
            el = page.query_selector(sel)
            if el:
                return el
        except Exception:
            continue
    return None


def _looks_logged_out(page) -> bool:
    body = ""
    try:
        body = page.inner_text("body")[:4000]
    except Exception:
        pass
    if _first_send_target(page) is not None:
        return False
    return bool(re.search(r"登录|Log ?in|扫码", body, re.I))


def ask_web_deepseek(question: str, *, timeout_s: int = 150, log=None, stop_event=None) -> str:
    """在**网页版** DeepSeek 里问一句，返回回答文本。不碰 API，不花钱。

    需要老大先在弹出的窗口里登录一次（登录态存在 state/browser-profile）。
    """
    from playwright.sync_api import sync_playwright

    question = str(question or "").strip()
    if not question:
        raise ValueError("问题不能为空")
    deadline = time.monotonic() + max(10, int(timeout_s))

    with sync_playwright() as pw:
        ctx = _launch(pw)
        try:
            page = ctx.new_page()
            page.set_default_timeout(min(5000, max(1000, (deadline - time.monotonic()) * 1000)))
            page.goto(DEEPSEEK_URL, timeout=min(20000, max(1000, (deadline - time.monotonic()) * 1000)), wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            if _looks_logged_out(page):
                raise NeedLogin("网页版 DeepSeek 还没登录：在弹出的浏览器里登录一次就好（只会问这一次）")
            box = _first_send_target(page)
            if box is None:
                raise NeedLogin("找不到输入框 —— 可能页面改版或仍停在登录页")

            _log(log, f"[web] 打开网页版 DeepSeek，问：{question[:60]}")
            box.click()
            box.fill(question)
            selector = ".ds-markdown, .markdown, [class*='markdown']"
            previous_nodes = page.query_selector_all(selector)
            previous_count = len(previous_nodes)
            previous_text = previous_nodes[-1].inner_text() if previous_nodes else ""
            page.keyboard.press("Enter")

            last, stable_since, answer = "", 0.0, ""
            while time.monotonic() < deadline:
                if stop_event is not None and stop_event.is_set():
                    raise Stopped("已停止")
                page.wait_for_timeout(1200)
                try:
                    nodes = page.query_selector_all(selector)
                    text = nodes[-1].inner_text() if nodes else ""
                    if len(nodes) <= previous_count and text == previous_text:
                        text = ""
                except Exception:
                    text = ""
                if text and text == last:
                    if stable_since == 0:
                        stable_since = time.monotonic()
                    elif time.monotonic() - stable_since >= 3.0 and page.query_selector("text=停止生成") is None:
                        answer = text
                        break
                else:
                    last, stable_since = text, 0.0
            if not answer:
                raise TimeoutError("网页回答未在时限内完成，未采用未完成的内容")
            _log(log, f"[web] 回答 {len(answer)} 字")
            return answer
        finally:
            try:
                ctx.close()
            except Exception:
                pass


_COMMENT_NOISE = re.compile(r"^(首页|推荐|关注|朋友|我的|搜索|登录|下载|打开|抖音|精选|直播|放映厅|"
                            r"\d+[.万]?[万wW]?$|赞|回复|分享|收藏|举报|评论|展开|收起)")


def collect_visible(page, limit: int = 40) -> dict:
    """抓「屏幕上能看见的文字」——标题、评论、弹幕都在这份文本里。

    不去赌抖音的类名（改版就挂），只取可见文本：稳、够用，而且天然只拿到
    「老大屏幕上真的出现了的东西」，不会偷偷多抓。
    """
    text = ""
    try:
        text = page.inner_text("body")
    except Exception:
        pass
    lines = [ln.strip() for ln in str(text).splitlines()]
    useful = []
    seen = set()
    for line in lines:
        if len(line) < 4 or len(line) > 120:
            continue
        if _COMMENT_NOISE.match(line):
            continue
        if line in seen:
            continue
        seen.add(line)
        useful.append(line)
        if len(useful) >= limit:
            break
    return {"texts": useful, "raw_chars": len(text)}


def watch_douyin(minutes: float = 10, *, log=None, stop_event=None, batch_seconds: int = 90) -> dict:
    """刷抖音：每条停留 10s±2，切下一条前再随机 2~5s。返回收集到的文本批次。"""
    from playwright.sync_api import sync_playwright

    end_at = time.time() + max(1, float(minutes)) * 60
    batches: list[list[str]] = []
    current: list[str] = []
    videos = 0
    last_flush = time.time()

    with sync_playwright() as pw:
        ctx = _launch(pw)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(DOUYIN_URL, timeout=90000, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            if _looks_logged_out(page):
                raise NeedLogin("抖音还没登录：在弹出的浏览器里扫码登录一次（登录态会留着）")

            _log(log, f"[douyin] 开始刷，计划 {minutes} 分钟")
            while time.time() < end_at:
                if stop_event is not None and stop_event.is_set():
                    raise Stopped("已停止")
                page.wait_for_timeout(int(random.uniform(8000, 12000) * 1000 / 1000))
                snap = collect_visible(page)
                if snap["texts"]:
                    current.extend(snap["texts"])
                videos += 1
                _log(log, f"[douyin] 第 {videos} 条（~{int(snap['raw_chars'])} 字可见文本）")

                # 切下一条：先随机停 2~5 秒（真人翻页前也会停一下）
                time.sleep(random.uniform(2, 5))
                moved = False
                for key in ("ArrowDown", "Space"):
                    try:
                        page.keyboard.press(key)
                        moved = True
                        break
                    except Exception:
                        continue
                if not moved:
                    for sel in ('[data-e2e="video-switch-next"]', '[data-e2e="feed-active-video"] + div'):
                        try:
                            page.click(sel, timeout=2000)
                            moved = True
                            break
                        except Exception:
                            continue
                if not moved:
                    _log(log, "[douyin] 翻不动了（可能弹了登录/验证框），先停一下")

                if current and (time.time() - last_flush >= batch_seconds or len(current) >= 60):
                    batches.append(current)
                    current = []
                    last_flush = time.time()
            if current:
                batches.append(current)
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    _log(log, f"[douyin] 结束：{videos} 条视频，{sum(len(b) for b in batches)} 行文本")
    return {"videos": videos, "batches": batches, "minutes": minutes}


def study_batch(lines: list[str], *, log=None, stop_event=None) -> list[dict]:
    """把一批可见文本交给**网页版** DeepSeek，让它挑出「梗/黑话」并给用法。"""
    body = "\n".join(str(x) for x in (lines or [])[:60])
    if len(body) < 40:
        return []
    prompt = (
        "下面是我从抖音评论区里抄下来的文字。请只挑出真正属于「网络梗 / 黑话 / 缩写 / 流行语」的词，"
        "每个词给一句「怎么用、怎么接」，不要解释来源，不要客套。\n"
        "严格按这个格式输出，一行一个，不要编号、不要表格、不要别的字：\n"
        "词|怎么用\n"
        "如果这段文字里没有梗，就只输出：无\n\n"
        "===== 评论原文 =====\n" + body
    )
    answer = ask_web_deepseek(prompt, log=log, stop_event=stop_event)
    if not answer or answer.strip() in ("无", "none", "None"):
        return []
    rows = parse_meme_lines(answer)
    _log(log, f"[study] 网页版给出 {len(rows)} 个词")
    return rows


def run_session(minutes: float = 10, *, log=None, stop_event=None) -> dict:
    """一键：刷抖音 → 网页版 DeepSeek 理解 → 写进本地梗库。全程 0 API 花费。"""
    started = datetime.now().isoformat(timespec="seconds")
    watched = watch_douyin(minutes, log=log, stop_event=stop_event)
    learned: list[dict] = []
    for index, batch in enumerate(watched["batches"], start=1):
        if stop_event is not None and stop_event.is_set():
            break
        _log(log, f"[study] 第 {index}/{len(watched['batches'])} 批：{len(batch)} 行")
        try:
            rows = study_batch(batch, log=log, stop_event=stop_event)
        except NeedLogin:
            raise
        except Exception as error:  # noqa: BLE001
            _log(log, f"[study] 这批没成：{error}")
            continue
        if rows:
            save_terms(rows)
            learned.extend(rows)
    return {"started": started, "finished": datetime.now().isoformat(timespec="seconds"),
            "videos": watched["videos"], "learned": len(learned), "terms": learned[:20]}


# ─────────────────────────────────────────────────────────────────────────────
# v36 自主上网学习：对外入口（真正的实现都在 core/learning/）
#   为什么要懒导入：本文件是面板/桥接的老入口，导入它不能带出数据库、认知层、
#   playwright 这些重活；`import core.webstudy` 必须还是又轻又安全。
# ─────────────────────────────────────────────────────────────────────────────
def _auto():
    from .learning import webstudy as module

    return module


def cfg() -> dict:
    """自主上网学习的生效配置（webstudy.json + learning.json 覆盖 + 内置默认）。"""
    return _auto().cfg()


def block_negative(text: str, *, soft_threshold: int | None = None) -> tuple[bool, str]:
    """负面内容纯函数闸：返回 (ok, reason)。命中即 False，reason 说明命中什么。"""
    from .learning import webfilter

    level = int(soft_threshold) if soft_threshold else _auto().soft_threshold()
    return webfilter.block_negative(text, soft_threshold=level)


def scan_negative(text: str) -> dict:
    """命中明细（哪些词、哪一类、硬还是软），排查用。"""
    from .learning import webfilter

    return webfilter.scan(text, soft_threshold=_auto().soft_threshold())


def filter_selftest() -> str:
    """负面词表正反例自测（命令行 `--selftest` 用的就是它）。"""
    from .learning import webfilter

    return webfilter.format_selftest(soft_threshold=_auto().soft_threshold())


def pick_topic(force_topic: str = "") -> dict | None:
    """这一轮该学什么（好奇心引擎 / 知识缺口 / 种子，自动去重）。"""
    return _auto().pick_topic(force_topic)


def run_autonomous_round(*, topic: str = "", force: bool = False, dry_run: bool = False,
                         log=None, stop_event=None) -> dict:
    """跑一轮自主上网学习（会开无头浏览器）。force=True 可跳过配额/间隔（仍走白名单+过滤）。"""
    return _auto().run_round(topic=topic, force=force, dry_run=dry_run, log=log,
                             stop_event=stop_event)


def tick(*, log=None, force: bool = False) -> dict:
    """调度入口：过闸（开关/配额/间隔/疲劳/联网预算）后跑一轮；不想跑就返回 skipped。"""
    return _auto().tick(log=log, force=force)


def status() -> dict:
    """配额、白名单、拦截统计、最近几轮 —— 面板/排查用。"""
    return _auto().status()


def dry_run() -> str:
    """静态 HTML 自检：不开浏览器、不连网、不写库。"""
    return _auto().format_dry_run()


def main(argv: list[str] | None = None) -> int:
    """python -m core.webstudy [--selftest | --dry-run | --status | --once [--force]]"""
    return _auto().main(argv)


if __name__ == "__main__":
    raise SystemExit(main())

