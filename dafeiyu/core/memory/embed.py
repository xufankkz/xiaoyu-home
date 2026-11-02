# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""记忆检索基础设施：嵌入后端（TF-IDF 兜底）+ 概念索引。

规格允许「无法加载本地嵌入模型时用 TF-IDF 兜底」。默认就是 TF-IDF（中文 bigram 余弦），
装了 sentence-transformers 再把 backend 切成 local_st 即可，接口不变。
"""

from __future__ import annotations

import math
from functools import lru_cache

from .. import settings
from ..text import bigrams


def backend() -> str:
    return str(settings.app().get("embedder", {}).get("backend", "tfidf"))


@lru_cache(maxsize=4096)
def _vec(text: str) -> tuple[tuple[str, float], ...]:
    counts: dict[str, float] = {}
    for tok in bigrams(text or ""):
        counts[tok] = counts.get(tok, 0.0) + 1.0
    if not counts:
        return ()
    norm = math.sqrt(sum(v * v for v in counts.values()))
    return tuple(sorted((k, v / norm) for k, v in counts.items()))


def similarity(query: str, text: str) -> float:
    """余弦相似度 0~1（bigram 词频，长度归一）。"""
    a = dict(_vec(query or ""))
    if not a:
        return 0.0
    b = dict(_vec(text or ""))
    if not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    return round(sum(v * b.get(k, 0.0) for k, v in a.items()), 4)


def similarities(query: str, texts: list[str]) -> list[float]:
    return [similarity(query, t) for t in texts]


def clear_cache() -> None:
    _vec.cache_clear()
