# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""自主学习浏览：开网页 → 等渲染 → 截图 / 抽图 → 交给视觉流水线。

底层 Playwright，用**系统 Edge**（channel="msedge"），不下载 Chromium。
没装 playwright 或起不来时 available()=False，学习流程照常走文字路径，不阻塞。

预算与安全：
- 单次学习会话最多 20 次（config/vision.json → session_limit）
- 域名白名单（非空时只放行）/ 黑名单（默认拦登录、支付、邮箱）
- 截图归档 learning/vision_snapshots/<日期>/，超 30 天只留摘要与概念
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from .. import settings
from ..entity import now_iso
from ..repo import system as sr
from ..text import clip

SESSION_FILE = "vision_session.json"


def cfg() -> dict:
    return settings.load("vision")


def _root(sub: str) -> Path:
    return settings.ROOT / str(cfg().get(sub, ""))


def _session() -> dict:
    raw = settings.read_state(SESSION_FILE, {})
    today = datetime.now().strftime("%Y-%m-%d")
    if str(raw.get("date")) != today:
        raw = {"date": today, "count": 0}
        settings.write_state(SESSION_FILE, raw)
    return raw


def _bump(n: int = 1) -> int:
    data = _session()
    data["count"] = int(data.get("count") or 0) + n
    settings.write_state(SESSION_FILE, data)
    return data["count"]


def budget_left() -> int:
    return max(0, int(cfg().get("session_limit", 20)) - int(_session().get("count") or 0))


def _domain_allowed(url: str) -> tuple[bool, str]:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False, "不是合法网址"
    allow = [str(d).lower() for d in (cfg().get("allow_domains") or [])]
    deny = [str(d).lower() for d in (cfg().get("deny_domains") or [])]
    if any(host == d or host.endswith("." + d) for d in deny):
        return False, f"域名在黑名单：{host}"
    if allow and not any(host == d or host.endswith("." + d) for d in allow):
        return False, f"域名不在白名单：{host}"
    return True, ""


def _shot_path(url: str, prefix: str = "shot") -> Path:
    folder = _root("snapshot_dir") / f"{datetime.now():%Y-%m-%d}"
    folder.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(f"{url}|{now_iso()}".encode("utf-8")).hexdigest()[:10]
    return folder / f"{prefix}-{digest}.png"


class WebVisionTool:
    name = "web_vision"

    def available(self) -> bool:
        try:
            import playwright  # noqa: F401

            return True
        except Exception:
            return False

    # ── 工具 1：打开网页截图 ──
    def browse_and_screenshot(self, url: str, wait_seconds: float | None = None,
                              full_page: bool = True) -> dict:
        if not self.available():
            return {"ok": False, "error": "playwright 未安装（pip install playwright）"}
        ok, reason = _domain_allowed(url)
        if not ok:
            sr.audit_add("vision", url[:120], "browse_blocked", {"reason": reason})
            return {"ok": False, "error": reason}
        if budget_left() <= 0:
            return {"ok": False, "error": "本次学习会话的截图预算用完了"}

        wait_seconds = float(wait_seconds if wait_seconds is not None
                             else cfg().get("browse_wait_seconds", 3))
        path = _shot_path(url)
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(channel=str(cfg().get("browse_channel", "msedge")),
                                            headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                page.wait_for_timeout(int(wait_seconds * 1000))
                title = page.title()
                page.screenshot(path=str(path), full_page=bool(full_page))
                browser.close()
        except Exception as exc:  # noqa: BLE001
            sr.audit_add("vision", url[:120], "browse_failed", {"error": str(exc)[:300]})
            return {"ok": False, "error": f"截图失败：{exc}"}

        _bump()
        sr.audit_add("vision", url[:120], "screenshot",
                     {"path": str(path), "title": title, "full_page": bool(full_page)})
        return {"ok": True, "path": str(path), "title": title, "url": url,
                "kind": "screenshot", "full_page": bool(full_page)}

    # ── 工具 2：抽页面上的主要图片 ──
    def extract_images_from_page(self, url: str, min_size: tuple[int, int] | None = None,
                                 max_count: int | None = None) -> dict:
        if not self.available():
            return {"ok": False, "error": "playwright 未安装"}
        ok, reason = _domain_allowed(url)
        if not ok:
            sr.audit_add("vision", url[:120], "extract_blocked", {"reason": reason})
            return {"ok": False, "error": reason}
        if budget_left() <= 0:
            return {"ok": False, "error": "本次学习会话的截图预算用完了"}

        c = cfg()
        min_w, min_h = (min_size or tuple(c.get("browse_min_size") or (200, 200)))[:2]
        max_count = int(max_count or c.get("browse_max_images", 10))
        folder = _root("snapshot_dir") / f"{datetime.now():%Y-%m-%d}"
        folder.mkdir(parents=True, exist_ok=True)
        saved = []
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(channel=str(c.get("browse_channel", "msedge")),
                                            headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(int(float(c.get("browse_wait_seconds", 3)) * 1000))
                items = page.eval_on_selector_all(
                    "img",
                    "els => els.map(e => ({src: e.currentSrc || e.src, w: e.naturalWidth, h: e.naturalHeight}))",
                )
                seen = set()
                for item in items or []:
                    src = str(item.get("src") or "")
                    if not src.startswith("http") or src in seen:
                        continue
                    if int(item.get("w") or 0) < min_w or int(item.get("h") or 0) < min_h:
                        continue
                    seen.add(src)
                    try:
                        resp = page.request.get(src, timeout=15000)
                        if not resp.ok:
                            continue
                        body = resp.body()
                        if len(body) < 3000:
                            continue
                        suffix = Path(urlparse(src).path).suffix.lower() or ".jpg"
                        if suffix not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
                            suffix = ".jpg"
                        target = folder / f"page-{hashlib.sha1(src.encode()).hexdigest()[:10]}{suffix}"
                        target.write_bytes(body)
                        saved.append({"path": str(target), "src": src,
                                      "w": int(item.get("w") or 0), "h": int(item.get("h") or 0)})
                        _bump()
                        if len(saved) >= max_count:
                            break
                    except Exception:
                        continue
                title = page.title()
                browser.close()
        except Exception as exc:  # noqa: BLE001
            sr.audit_add("vision", url[:120], "extract_failed", {"error": str(exc)[:300]})
            return {"ok": False, "error": f"抽图失败：{exc}"}

        sr.audit_add("vision", url[:120], "extract",
                     {"count": len(saved), "title": title,
                      "files": [Path(s["path"]).name for s in saved[:10]]})
        return {"ok": True, "images": saved, "title": title, "url": url, "kind": "page_image"}


def explore(url: str, question: str = "", *, max_images: int = 3) -> dict:
    """一次完整的「看图学习」：截图 + 抽图 → 视觉流水线 → 汇总摘要。"""
    from . import pipeline

    tool = WebVisionTool()
    if not tool.available():
        return {"ok": False, "error": "web_vision 不可用"}
    out = {"ok": True, "url": url, "shots": [], "images": [], "summaries": []}
    shot = tool.browse_and_screenshot(url)
    if shot.get("ok"):
        out["shots"].append(shot["path"])
        res = pipeline.analyze(shot["path"], question=question, kind="screenshot",
                               source=url, created_from="learning", label=f"网页截图 {url}",
                               feature_tag="vision_browse")
        out["summaries"].append(res.get("summary", ""))
    got = tool.extract_images_from_page(url, max_count=max_images)
    if got.get("ok"):
        for image in got["images"][:max_images]:
            out["images"].append(image["path"])
            res = pipeline.analyze(image["path"], question=question, kind="page_image",
                                   source=url, created_from="learning",
                                   label=f"网页插图 {Path(image['path']).name}",
                                   feature_tag="vision_browse")
            out["summaries"].append(res.get("summary", ""))
    out["summary"] = clip("\n\n".join(s for s in out["summaries"] if s), 4000)
    out["ok"] = bool(out["shots"] or out["images"])
    return out


def cleanup(days: int | None = None) -> dict:
    """超期截图只留摘要：删 png/jpg，vision_asset 里的记录留着。"""
    days = int(days if days is not None else cfg().get("retention_days", 30))
    cutoff = datetime.now() - timedelta(days=days)
    root = _root("snapshot_dir")
    removed = 0
    if not root.exists():
        return {"removed": 0, "days": days}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            continue
        try:
            if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                path.unlink()
                removed += 1
        except Exception:
            continue
    return {"removed": removed, "days": days}


def status() -> dict:
    tool = WebVisionTool()
    return {"available": tool.available(), "budget_left": budget_left(),
            "session": _session(), "snapshot_dir": str(_root("snapshot_dir"))}
