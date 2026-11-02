"""Read-only shared dashboard for the existing fictional character settings."""
from __future__ import annotations

import random

from .. import affective, homeostasis, hormones
from ..memory import longterm
from . import daily, policy, reward, vector


def view(day: str = "") -> dict:
    vec = vector.read_snapshot()
    state = vector.status(vec)
    character = affective.status(vec=vec)
    needs, parameters = homeostasis.status(), hormones.status()
    # A dashboard view must not consume the response engine's random sequence.
    advice = policy.build(vec, rng=random.Random(0))
    return {"character": character, "emotion": character["emotion"],
            "dashboard": {"emotion": state, "needs": needs, "parameters": parameters,
                          "policy": {"tone": advice.tone, "tone_hint": policy.tone_hint(advice),
                                     "max_chars": advice.max_chars, "chitchat": advice.chitchat,
                                     "reason": advice.reason, "advisory": True},
                          "memory_gates": longterm.gates(vec), "events": reward.recent(10)},
            "daily": daily.view(day)}
