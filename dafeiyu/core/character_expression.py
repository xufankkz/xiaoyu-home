"""One local presentation plan for the fictional character, without model calls."""
from datetime import datetime

LABELS = {'joy': '愉悦', 'happiness': '幸福', 'sadness': '难过',
          'inferiority': '有些不自信', 'shyness': '害羞'}


def plan(vec, state, now=None):
    now = now or datetime.now()
    values = sorted(((key, float(vec.get(key))) for key in LABELS), key=lambda p: p[1], reverse=True)
    primary, value = values[0]
    previous = state.expression or {}
    since = previous.get('since', now.isoformat(timespec='seconds'))
    try:
        recent = 0 <= (now - datetime.fromisoformat(since)).total_seconds() < 120
    except (TypeError, ValueError):
        recent = False
    old = previous.get('primary')
    if old in LABELS and recent and value - float(vec.get(old)) < 4:
        primary, value = old, float(vec.get(old))
    if previous.get('primary') != primary:
        since = now.isoformat(timespec='seconds')
    secondary = next((k for k, v in values if k != primary and v >= 15 and v >= value * .3), '')
    quiet = bool(state.rest_anticipation or state.energy_cycle < .2 or primary in {'sadness', 'inferiority', 'shyness'})
    tone = 'playful' if primary == 'joy' and value >= 65 and not quiet else 'gentle'
    label = LABELS[primary] + ('，也带着' + LABELS[secondary] if secondary else '')
    reason = '依据当前情绪数值和角色精力决定表达'
    if state.rest_anticipation:
        label += '，准备休息'; reason += '；休息时放轻语气'
    elif state.energy_cycle < .2:
        label += '，想慢一点'; reason += '；精力较低时减少兴奋表达'
    block = f'【共用表达状态】{label}。' + ('表达轻柔、简短，保留关心。' if quiet else '自然轻快，语气与实际心情一致。')
    block += '不必每次解释情绪数值，不用反话掩饰低落，不改变既有人设与称呼。'
    return {'primary': primary, 'secondary': secondary, 'since': since, 'label': label,
            'tone': tone, 'quiet': quiet, 'reason': reason, 'block': block}
