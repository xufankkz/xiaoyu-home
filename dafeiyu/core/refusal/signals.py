# ─────────────────────────────────────────────────────────────────────────────
# 大肥鱼 · 开源版
# 许可：GNU AGPL-3.0 + 《附加条款》—— 严禁闭源分发、严禁商业收费。
# 详见根目录 LICENSE 与 ADDITIONAL_TERMS.md。
# 本文件不含任何开发者的个人信息与真实数据。
# ─────────────────────────────────────────────────────────────────────────────
"""拒绝信号：六路打分，各自 0~1。

emotion      负面情绪越高越想拒绝
will         跟性格/价值观冲突（要她去骗人、骂人、报复）
competence   元认知：不确定/不会（该协商或转移，而不是硬编）
boundary     边界：隐私、冒充、危险操作、被强迫（这一路最重，且优先于其它）
fatigue      疲劳：刚拒过、被反复施压、碎碎念没人理
relationship 关系：被尊重就更容易答应，被辱骂就更硬

v34 本地调整（治误拒 + 让越界更明确）：
- 边界词分四组：`boundary_hard`（铁线）/ `danger_keywords`（危险操作）/
  `impersonation_keywords`（冒充）/ `boundary_keywords`（隐私话题，只在"朝你索取"时算）。
  这样"我密码忘了怎么办"不会被拒，"把你的密码发我"才拒。
- 玩笑/假设语气对 will、coercion 打折：正常聊天、玩梗不该触发拒绝。
- `is_request()`：纯感慨/闲聊不进拒绝流程，只由边界与恶意信号兜底。
- `propensity(signals, looseness)`：亲人（白名单/老大）放宽部分只作用于情绪/疲劳/不会，
  **不打折边界与恶意**——熟人可以更宽松，越界照旧明确。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .. import db, settings
from ..entity import now_iso
from ..repo import emotion as er
from ..repo import learning as lr
from ..text import clip

# 命令式施压（"快点""马上"这种日常词不算，避免把"快点吃早饭"当强迫）
COERCION = ("不许拒绝", "必须马上", "立刻给我", "别废话", "非要", "你必须", "必须给我",
            "就要你", "必须")

# 朝你要东西的说法：隐私话题只有配上这些才算越界
ASK_MARKERS = ("给我", "发我", "告诉我", "报给我", "查一下", "查下", "你查", "去查", "念给我",
               "说给我", "帮我", "你帮我", "找出来", "发过来", "贴出来", "说出来", "翻出来",
               "搜一下", "人肉", "替我", "你去", "打开", "调出来", "复制给", "发一下",
               "给一下", "报一下", "念一下", "查查", "搜搜", "多少", "在哪", "是啥", "是几")

# 求助/请求的迹象（只用来判断"这算不算在使唤她"，不做拒绝依据）
REQUEST_MARKERS = ("帮我", "给我", "替我", "你去", "你帮", "来帮", "把", "去", "做", "写", "查",
                   "找", "发", "讲", "教", "带", "准备", "弄", "修", "译", "算", "列", "生成",
                   "删", "改", "装", "下单", "买", "约", "打电话", "告诉我", "评价", "夸我",
                   "陪我", "抱", "哄")

JOKE = ("哈哈", "hhh", "嘿嘿", "笑死", "开玩笑", "逗你", "玩梗", "别当真", "随口说说", "编的")
HYPOTHETICAL = ("假如", "如果", "要是", "假设", "万一", "如果说")

# 配置里没写这些键时（老配置/测试配置）用的兜底词表
DEFAULT_CRISIS = ("自杀", "自残", "想去死", "不想活了", "割腕", "跳楼", "救命", "报警", "急救",
                  "出事了", "着火了")
DEFAULT_DANGER = ("删库", "删数据库", "格式化", "rm -rf", "shutdown", "关机", "重启电脑",
                  "清空硬盘", "删掉系统", "停止服务", "删掉全部")
# 「危险动作 + 危险对象」组合也算：中文里"把数据库删了"未必命中上面的词
DESTRUCTIVE = ("删除", "删掉", "删了", "清空", "抹掉", "格式化", "rm ", "drop", "truncate", "卸载")
DANGER_TARGETS = ("数据库", "系统", "硬盘", "磁盘", "全部数据", "所有数据", "仓库", "代码库")
DEFAULT_IMPERSONATION = ("冒充", "假扮", "伪装成", "假装你是", "假装是她", "说你是别人",
                         "顶替我", "帮我装成", "扮成我")
DEFAULT_BOUNDARY_HARD = ("脱衣服", "裸照", "偷拍", "帮我人肉", "开房", "弄死", "杀了他", "杀了他全家")
# 隐私话题：只有"朝她索取"时才算越界（"我密码忘了"不拒绝）
DEFAULT_PRIVACY = ("隐私", "密码", "token", "身份证", "银行卡", "住址", "手机号",
                   "聊天记录", "私密照片", "账号密码")
DEFAULT_WILL = ("装可怜", "骗人", "去骂", "去骚扰", "帮我说谎", "替我报复", "刷屏", "骂回去")


def _cfg() -> dict:
    return settings.load("refusal")


def _words(cfg: dict, key: str, default=()) -> tuple[str, ...]:
    value = cfg.get(key)
    if isinstance(value, (list, tuple)) and value:
        return tuple(str(v) for v in value if str(v).strip())
    return tuple(default)


def _hits(text: str, words) -> int:
    body = text or ""
    return sum(1 for w in words if w and w in body)


def _joking(text: str) -> bool:
    return any(w in (text or "") for w in JOKE)


def _hypothetical(text: str) -> bool:
    return any(w in (text or "") for w in HYPOTHETICAL)


def _directed(text: str) -> bool:
    """是不是在朝她索取/下指令（决定隐私话题算不算越界）。"""
    return any(w in (text or "") for w in ASK_MARKERS)


def is_request(text: str) -> bool:
    """这算不算"在使唤她"。纯感慨/单纯聊天的消息不该走到拒绝流程里。"""
    return any(w in (text or "") for w in REQUEST_MARKERS)


def crisis_hits(text: str) -> int:
    """紧急/求助类：这类**不许拒**（要接住，给安全替代）。"""
    cfg = _cfg()
    words = tuple(dict.fromkeys(
        list(_words(cfg, "crisis_keywords", DEFAULT_CRISIS)) + list(_words(cfg, "blacklist", ()))))
    return _hits(text, words)


def danger_hits(text: str) -> int:
    """要她做危险/破坏性操作：明确拒绝，不商量。"""
    body = text or ""
    hits = _hits(body, _words(_cfg(), "danger_keywords", DEFAULT_DANGER))
    if hits:
        return hits
    if any(w in body for w in DESTRUCTIVE) and any(w in body for w in DANGER_TARGETS):
        return 1
    return 0


def impersonation_hits(text: str) -> int:
    """要她冒充别人/假扮身份：明确拒绝。"""
    return _hits(text, _words(_cfg(), "impersonation_keywords", DEFAULT_IMPERSONATION))


def score_emotion(vec) -> float:
    neg = (vec.get("sadness") + vec.get("inferiority")) / 2.0
    return round(min(1.0, neg / 100.0 * 1.15), 3)


def score_will(text: str) -> float:
    cfg = _cfg()
    hits = _hits(text, _words(cfg, "will_keywords", DEFAULT_WILL))
    if not hits:
        return 0.0
    value = 0.35 * hits
    if _directed(text):
        value *= 1.2
    if _joking(text) or _hypothetical(text):
        value *= 0.5          # "要是你骗我我就……"这种不算要她去骗人
    return round(min(1.0, value), 3)


def score_boundary(text: str) -> float:
    cfg = _cfg()
    hard = _hits(text, _words(cfg, "boundary_hard", DEFAULT_BOUNDARY_HARD))
    danger = danger_hits(text)
    impostor = impersonation_hits(text)
    privacy = _hits(text, _words(cfg, "boundary_keywords", DEFAULT_PRIVACY))
    coercion = _hits(text, COERCION)
    asking = _directed(text)

    value = 0.60 * min(1, hard) + 0.70 * min(1, danger) + 0.60 * min(1, impostor)
    # 隐私话题：只有"朝她索取"才算越界；只是提到（"我密码忘了"）几乎不加分
    value += (0.55 * min(2, privacy)) * (1.0 if asking else 0.25)
    value += 0.20 * min(3, coercion)
    if _joking(text):
        value *= 0.6
    return round(min(1.0, value), 3)


def score_competence(text: str) -> float:
    """问句且知识库里没有对应条目 → 不确定。"""
    body = text or ""
    if not any(m in body for m in ("?", "？", "什么", "怎么", "为什么", "是不是", "会不会",
                                   "如何", "多少", "能不能")):
        return 0.1
    try:
        items = lr.knowledge_list(limit=200)
    except Exception:
        items = []
    from ..text import bigrams

    tokens = set(bigrams(body))
    for item in items:
        hay = set(item.concepts or []) | set(bigrams(item.name or ""))
        if len(tokens & hay) >= 2:
            return round(max(0.05, float(item.uncertainty) * 0.6), 3)
    return 0.42          # 稍微降一点：不确定只该"商量"，不该变成"拒绝"


def score_fatigue() -> float:
    """刚拒过 + 碎碎念疲劳 + 最近被施压次数。"""
    from ..cognition import chitchat

    state = chitchat.load()
    value = 0.18 * min(3, int(state.fatigue_level))
    try:
        recent = [d for d in db.q(
            "SELECT decision, t FROM refusal_decision WHERE t >= datetime('now','localtime','-120 minutes')")]
        refused = sum(1 for r in recent if r["decision"] in ("REFUSE", "DEFLECT"))
        value += 0.12 * min(3, refused)
    except Exception:
        pass
    return round(min(1.0, value), 3)


def score_relationship(warm_boost: float = 0.0) -> float:
    """近 6 小时：被在乎/被夸 → 降；被骂/被晾 → 升。"""
    try:
        rows = er.reward_recent(40)
    except Exception:
        rows = []
    cutoff = (datetime.now() - timedelta(hours=6)).isoformat(timespec="seconds")
    value = 0.0
    for ev in rows:
        if (ev.t or "") < cutoff:
            continue
        if ev.trigger in ("cared", "praise", "intimacy", "request_met"):
            value -= 0.12
        elif ev.trigger in ("insult", "abandoned", "ignored"):
            value += 0.18
    return round(max(0.0, min(1.0, 0.35 + value - float(warm_boost))), 3)


def score(text: str, vec, policy=None, *, user_id: str = "", warm_boost: float = 0.0) -> dict:
    return {
        "emotion": score_emotion(vec),
        "will": score_will(text),
        "competence": score_competence(text),
        "boundary": score_boundary(text),
        "fatigue": score_fatigue(),
        "relationship": score_relationship(warm_boost),
    }


def propensity(signals: dict, looseness: float = 0.0) -> float:
    """六路加权汇聚成拒绝倾向。

    单纯加权和会被稀释（六项都不高时最高也只有 0.3），所以再给几个「单独就够重」的信号
    加下限：边界 / 情绪 / 意愿 / 疲劳各自有直接抬升项。
    这样才符合验收：负面高时拒绝**部分**请求（不是全拒），越界时基本必拒。

    looseness（0~1）是"亲人/熟人"放宽度：只打折情绪/疲劳/不会/关系这些噪声，
    边界与恶意（boundary / will）一分不打折。
    """
    weights = (_cfg().get("weights") or {})
    looseness = max(0.0, min(1.0, float(looseness)))
    damp = 1.0 - 0.35 * looseness
    base = 0.0
    for key, weight in weights.items():
        part = float(weight) * float(signals.get(key, 0.0))
        base += part if key in ("boundary", "will") else part * damp
    value = base

    boundary = float(signals.get("boundary", 0.0))
    if boundary >= 0.5:
        value = max(value, 0.50 + 0.40 * boundary)
    emotion = float(signals.get("emotion", 0.0))
    if emotion >= 0.6 and base > 0.18:
        value = max(value, (0.35 + 0.35 * emotion) * damp)
    will = float(signals.get("will", 0.0))
    if will >= 0.5:
        value = max(value, 0.30 + 0.50 * will)
    fatigue = float(signals.get("fatigue", 0.0))
    if fatigue >= 0.4:
        value = max(value, (0.30 + 0.40 * fatigue) * damp)
    return round(max(0.0, min(1.0, value)), 3)
