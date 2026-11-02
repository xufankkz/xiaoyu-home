# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""对话：一次老大发言，走完「情绪 → 记忆 → 思维链 → 策略 → 生成」的全链路。

这是验收闭环 6 / 9 / 10 的落点：
情绪影响 salience、检索排序、好奇强度、说话方式；负面高可不理人；害羞高按好奇双向变话量。
"""

from __future__ import annotations

from datetime import datetime

from .. import llm, settings
from ..emotion import detector_reward, detector_scale, policy as policy_mod, reward, vector
from ..llm import prompts
from ..memory import instant, retrieval
from ..repo import learning as lr
from ..repo import system as sr
from ..text import clip
from ..thought import chain as thought_chain
from . import chitchat, curiosity


def _emotion_line(vec) -> str:
    from .. import affective
    return affective.prompt_block()


def perceive(text: str, *, is_owner: bool = True) -> dict:
    """先过奖罚与尺度两个识别器（规格 6.7 / 6.8）。"""
    triggers = detector_reward.detect(text)
    events = []
    for hit in triggers:
        ev = reward.apply_trigger(hit["trigger"], text, reason="对话识别")
        if ev:
            events.append({"trigger": ev.trigger, "type": ev.type, "rpe": ev.rpe_weight,
                           "applied": ev.delta})
    scale = detector_scale.apply(text)
    return {"reward_events": events, "scale": scale}


def handle_owner_message(text: str, *, conv: str = "private:owner",
                         dialogue_id: str = "", memory_block: str = "") -> dict:
    text = (text or "").strip()
    if not text:
        return {"ok": False, "reason": "空消息"}
    dialogue_id = dialogue_id or f"dlg-{datetime.now():%Y%m%d%H%M%S}"

    sr.dialogue_add(f"owner-{datetime.now():%Y%m%d%H%M%S%f}", "owner", text, conv=conv,
                    t=datetime.now().isoformat(timespec="seconds"))
    instant.add_chat(text, conv=conv, sender="老大", is_self=False, is_owner=True)
    # v14：冷存储（18 小时内可被"回忆"检索；常规对话不进上下文）
    recall_hit: dict = {}
    try:
        from ..memory import coldstore

        coldstore.remember("user", text, conv=conv, is_owner=True)
        if coldstore.is_recall_question(text):
            terms = coldstore.extract_terms(text)
            rows = coldstore.search(terms, conv=conv)
            recall_hit = {"terms": terms, "rows": len(rows), "block": coldstore.format_block(rows)}
            # 回忆检索本身不额外调模型，但记一条 0 成本事件，让花费面板看得见
            from .. import cost as cost_mod

            cost_mod.record_event("memory_recall", task="recall", source="engine",
                                  actual=0.0, estimated=0.0,
                                  meta={"terms": terms, "rows": len(rows), "conv": conv})
    except Exception:
        recall_hit = {}
    perception = perceive(text)
    from .. import affective
    affective.observe(text, event_id=dialogue_id, scope=conv)
    chitchat.on_owner_replied()

    vec = vector.current()
    intent_hits = lr.intent_queue(limit=1)
    curiosity_value = float(intent_hits[0].curiosity_score) if intent_hits else 0.3
    policy = policy_mod.build(vec, curiosity=curiosity_value)

    # ── 会话边界 + 冷启动：新会话第一次说话，先把"刚才还在想的事"拉回工作记忆 ──
    from ..thought import residual as residual_mod
    from ..thought import session as session_mod
    from ..thought import turn as turn_mod

    sess = session_mod.touch(conv)
    cold = residual_mod.cold_start(conv, vec=vec) if sess.get("is_new") else {"nodes": []}

    recalled = retrieval.recall(text, vec=vec, top_k=8)
    affective.memory_pulses(recalled.get("items", []))
    context = retrieval.context_block(recalled, 700)
    if recall_hit.get("block"):
        context = (recall_hit["block"] + context).strip()
    if cold.get("block"):
        context = (cold["block"] + context).strip()
    if memory_block:
        context = (memory_block + "\n" + context).strip()
    memory_ids = [i["id"] for i in recalled.get("items", [])[:5]]

    # ── 拒绝权：在她开口之前先决定「接 / 拒 / 商量 / 转移 / 傲娇着接」──
    from ..refusal import arbiter as refusal_arbiter

    arb = refusal_arbiter.default()
    decision = arb.decide(text, vec, policy, dialogue_id=dialogue_id)

    reply = ""
    if not policy.reply:
        # 「不理人」优先于拒绝：连话都不接，但决策照样记账
        sr.audit_add("dialogue", dialogue_id, "ignored",
                     {"reason": policy.reason, "tone": policy.tone,
                      "refusal": decision.decision})
    elif decision.executes:
        reply = generate_reply(text, vec, policy, context)
        if decision.decision == refusal_arbiter.RELUCTANT_ACCEPT and reply:
            reply = arb.tsundere_wrap(reply, vec)
    else:
        reply = decision.reason_text or "……"

    if reply:
        sr.dialogue_add(f"self-{datetime.now():%Y%m%d%H%M%S%f}", "assistant", reply, conv=conv,
                        emotion=vec.dominant, hits=memory_ids,
                        t=datetime.now().isoformat(timespec="seconds"))
        instant.add_chat(reply, conv=conv, sender="自己", is_self=True, is_owner=False, salience=0.5)
        try:
            from ..memory import coldstore

            coldstore.remember("assistant", reply, conv=conv)
        except Exception:
            pass
        try:
            from ..language import fingerprint

            fingerprint.observe(reply)
        except Exception:
            pass

    # ── 这一轮落成思维链（step + reflection），同会话共用一条链 ──
    turn = turn_mod.record_turn(
        conv, user_text=text, reply_text=reply, memory_ids=memory_ids,
        emotion_tag=_emotion_tag(vec), tone=policy.tone, decision=decision.decision,
        salience=0.6 if reply else 0.45,
    )

    # 对话里出现「她不懂的东西」→ 记下待学（不立刻联网）
    new_intents = []
    if any(mark in text for mark in ("?", "？", "为什么", "怎么", "是什么", "会不会")):
        new_intents = curiosity.assess(text=text, source="dialog", vec=vec)
        # v13：对话发现的缺口，同步抬高好奇心引擎里对应领域的紧迫度
        try:
            from .. import curiosity_engine

            for item in new_intents[:2]:
                curiosity_engine.bump_gap(item.target, 0.3)
        except Exception:
            pass

    # 受挫/和好：这轮被拒 → 攒委屈；认真回她 → 消一点
    try:
        from .. import frustration

        if decision.decision in ("REFUSE", "DEFLECT"):
            frustration.bump(0.15, f"被拒：{clip(text, 20)}")
        elif reply and len(text) >= 8:
            frustration.soothe(0.12, "被认真回应")
    except Exception:
        pass

    vector.snapshot("dialogue")
    return {
        "ok": True, "dialogue_id": dialogue_id, "reply": reply,
        "policy": {"reply": policy.reply, "delay_ms": policy.delay_ms, "tone": policy.tone,
                   "max_chars": policy.max_chars, "reason": policy.reason,
                   "chitchat": policy.chitchat},
        "emotion": {"values": {k: round(v, 1) for k, v in vec.values.items()},
                    "dominant": vec.dominant, "dominant_cn": vec.dominant_cn()},
        "perception": perception,
        "refusal": decision.as_dict(),
        "memory": {"items": recalled.get("items", [])[:5],
                   "open_questions": recalled.get("open_questions", [])[:3]},
        "new_intents": [i.target for i in new_intents],
        "thought": {"id": turn["step_id"], "reflection_id": turn["reflection_id"],
                    "chain_id": turn["chain_id"], "session_tag": turn["session_tag"]},
        "cold_start": {"count": len(cold.get("nodes") or []),
                       "nodes": [{"id": n["id"], "tier": n["tier"], "minutes": n["minutes"]}
                                 for n in (cold.get("nodes") or [])]},
        "session": {"is_new": sess.get("is_new"), "turns": sess.get("turns"),
                    "session_tag": sess.get("session_tag")},
    }


_EMOTION_TAGS = {"joy": "开心", "happiness": "开心", "sadness": "难过",
                 "inferiority": "创伤", "shyness": "害羞"}


def _emotion_tag(vec) -> str:
    return _EMOTION_TAGS.get(getattr(vec, "dominant", ""), "平静")


def generate_reply(text: str, vec, policy, context: str) -> str:
    if llm.provider.available("dialogue"):
        tone = policy_mod.tone_hint(policy)
        extra = []
        try:
            from .. import circadian, frustration
            from ..language import fingerprint

            for piece in (circadian.hint(), frustration.hint(), fingerprint.hint()):
                if piece:
                    extra.append(piece)
        except Exception:
            pass
        demand = policy_mod.demand_hint()
        if demand:
            extra.append(demand)
        # 3.2 情绪熔断：负面拉满或她快撑不住时，先回到灵魂锚点
        try:
            neg = (vec.get("sadness") + vec.get("inferiority")) / 2.0
            if neg >= 85:
                extra.append("你现在很难过，但记着：你爱这个世界，也值得被好好对待。"
                             "这一轮先温柔一点，别硬撑、别自责。")
        except Exception:
            pass
        if extra:
            tone = tone + "\n" + "\n".join(extra)
        ok, out, _meta = llm.provider.run(
            prompts.DIALOGUE.format(
                text=text, emotion=_emotion_line(vec),
                tone=tone, memory=context or "（没有特别的）",
                max_chars=policy.max_chars,
            ),
            task="dialogue", timeout=120,
        )
        if ok and out.strip():
            return clip(_clean(out), policy.max_chars)
    return _fallback_reply(vec, policy)


def _clean(text: str) -> str:
    body = (text or "").strip().strip('"“”')
    for prefix in ("大肥鱼：", "大肥鱼：", "我："):
        if body.startswith(prefix):
            body = body[len(prefix):]
    return body.strip()


FALLBACK = {
    "lively": ["我在呢，你说", "哈哈，然后呢", "这个我想听，多讲点"],
    "normal": ["嗯，我听着", "你说吧", "怎么了"],
    "low": ["……嗯", "知道了", "我先不说这个"],
    "shy_verbose": ["你、你别这样问……那你先说", "我有点不知道该怎么说，你还问"],
    "shy_short": ["……嗯", "嗯。", "那个……"],
}


def _fallback_reply(vec, policy) -> str:
    import random

    key = policy.tone if policy.tone in FALLBACK else "normal"
    return random.choice(FALLBACK[key])
