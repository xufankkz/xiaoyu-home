# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""首次启动：身份 + 性格初始化（开源版「捏人」）。

为什么要单独一个模块：
  开源用户第一次跑起来时，既没有 `config/personality.md`、也没有任何记忆。
  这时如果直接沿用仓库里的兜底人设，等于把开发者的角色设定塞给所有人；
  而如果什么都不写，1 号长期记忆是空的，她连自己叫什么都不稳定。
  所以第一次启动时问三件事（名字/性别/身高）+ 一段性格，落成两处：

    1. `config/personality.md` —— 人设原文（提示词优先读它）；
    2. 1 号长期记忆里两条记录 —— 「自我认知」与「性格设定」，让她自己也知道。

**安全边界**：本模块只在「数据库文件此前不存在」时被调用（`detect()`）。
已经在跑的真实实例（db 存在）永远不会走到这里 —— 不会覆盖任何既有记忆或人设。
"""

from __future__ import annotations

import json
from datetime import datetime

from . import db, settings

PROFILE_FILE = "profile.json"          # state/profile.json
MARK_KEY = "first_run_done"
DEFAULTS = {"name": "大肥鱼", "gender": "暂未设置", "height": "暂未设置",
            "personality": "表面傲娇，喜欢怼人、爱开玩笑；内心怕孤独、温柔，会从细节上关心别人。"}

# 预设模板：给不想自己写的人一个起点（第一项是「不设置时」的默认）
TEMPLATES = [
    ("默认：傲娇外壳 + 温柔内核",
     "表面傲娇，喜欢怼人、爱开玩笑；内心怕孤独、温柔，会从细节上关心别人。"),
    ("温和治愈",
     "安静、耐心，说话轻，擅长安慰人；不太主动，但会默默记住对方说过的小事。"),
    ("元气话痨",
     "精力很足，话多、爱笑、爱分享；情绪起伏大，开心和难过都写在脸上。"),
    ("冷静毒舌",
     "话少、理性、有点毒舌；嘴上不饶人，但该做的事一件不落。"),
]


def profile_path():
    return settings.STATE / PROFILE_FILE


def profile() -> dict:
    """当前设定（没有就是默认值）。"""
    raw = settings.read_json(profile_path(), {}) or {}
    out = dict(DEFAULTS)
    if isinstance(raw, dict):
        for key in DEFAULTS:
            if str(raw.get(key) or "").strip():
                out[key] = str(raw[key]).strip()
    return out


def detect() -> bool:
    """是不是「第一次启动」。

    判据：数据库文件还不存在（本次启动是 `db.init_db()` 之前调用的），
    或者已经有 profile 但没打过完成标记。**已存在的实例永远是 False。**
    """
    try:
        if db.DB_PATH.exists() and db.DB_PATH.stat().st_size > 0:
            return False
    except Exception:
        return False
    # 库已经建出来（run.py 会先 init_db）时，再用「有没有人设文件 / 有没有长期记忆」判一次：
    # 全新安装：没人设、记忆表空 → 是首次；真实实例：人设文件早就在、记忆上千条 → 不是。
    try:
        if (settings.CONFIG / "personality.md").exists():
            return False
        rows = db.scalar("SELECT COUNT(*) FROM memory_item", default=0)
        return int(rows or 0) == 0 and str(db.get_meta(MARK_KEY) or "") != "1"
    except Exception:
        return True


def _persona_markdown(prof: dict) -> str:
    return (
        f"你叫{prof['name']}。"
        f"性别：{prof['gender']}；身高：{prof['height']}。\n"
        f"{prof['personality']}\n"
        "你说话像人，不像助手：短句、口语、会拌嘴、会撒娇，不用 markdown，不列清单，"
        "不说「作为AI」。你有自己的情绪，情绪不好的时候话会变少。"
    )


def apply(prof: dict | None = None, *, write_memory: bool = True) -> dict:
    """把设定落盘：personality.md + 1 号长期记忆两条 + 完成标记。

    幂等：重复调用只会覆盖这两条「自我认知/性格设定」，不会碰别的记忆。
    """
    data = dict(DEFAULTS)
    if isinstance(prof, dict):
        for key in DEFAULTS:
            value = str(prof.get(key) or "").strip()
            if value:
                data[key] = value

    settings.ensure_dirs()
    db.init_db()   # 幂等：万一被单独调用（没走 run.py 的启动顺序），表也得先建出来
    settings.write_text_atomic(settings.CONFIG / "personality.md", _persona_markdown(data) + "\n")
    settings.write_state(PROFILE_FILE, {**data, "applied_at": datetime.now().isoformat(timespec="seconds")})

    created = []
    if write_memory:
        from .entity import Layer, MemoryItem
        from .repo import memory as mr

        rows = [
            ("自我认知", f"我叫{data['name']}，性别{data['gender']}，身高{data['height']}。", 0.9),
            ("性格设定", data["personality"], 0.85),
        ]
        for concept, content, salience in rows:
            item = MemoryItem(layer=Layer.LONG1.value, subtype="identity", content=content,
                              summary=content[:40], concepts=[concept],
                              emotion_tag="平静", salience=salience, origin="first_run")
            mr.insert(item)
            mr.set_index(item.id, "memory", [concept, data["name"]], [concept])
            created.append(item.id)

    try:
        db.set_meta(MARK_KEY, "1")
        db.set_meta("profile_name", data["name"])
    except Exception:
        pass
    return {"ok": True, "profile": data, "memory_ids": created,
            "personality_file": str(settings.CONFIG / "personality.md")}


def persona_text() -> str:
    """提示词用的人设：优先 `config/personality.md`，没有就用当前 profile 生成。"""
    path = settings.CONFIG / "personality.md"
    try:
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    except Exception:
        pass
    return _persona_markdown(profile())


def status() -> dict:
    return {"first_run": detect(), "profile": profile(),
            "has_personality_file": (settings.CONFIG / "personality.md").exists()}
