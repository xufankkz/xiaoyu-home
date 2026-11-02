"""Turn local connection signals and saved request outcomes into a safe UI status.

No provider requests, generated messages, raw errors, credentials or chat text.
"""
from datetime import datetime, timezone


REQUEST_REASONS = {
    'credentials': '上游接口鉴权失败，请检查聊天 Key 或接口权限',
    'balance': '上游接口余额或额度不足',
    'rate_limit': '上游接口限流，请稍后再试',
    'model_unavailable': '上游所配置的聊天模型不可用',
    'upstream_unavailable': '聊天请求连接失败或上游服务异常',
    'upstream_timeout': '聊天请求超时，可能是上游响应或网络连接问题',
    'chat_error': '最近一次聊天处理失败，现有记录不足以确定是否为上游问题',
}


def build_chat_status(status=None, bridge_error=None, now=None):
    now = now or datetime.now(timezone.utc)
    out = {'online': False, 'reason': '', 'message': '', 'note': '',
           'checked_at': now.isoformat(timespec='seconds'),
           'last_request_at': None, 'model_probe': False, 'refresh_seconds': 15}

    def unavailable(code, reason):
        out.update(code=code, reason=reason, message='当前不可聊天，原因：' + reason)
        return out

    if bridge_error:
        if bridge_error == 'auth':
            return unavailable('bridge_auth', '本地桥接状态读取未通过认证，暂时无法确认是否可聊天')
        return unavailable('bridge_unreachable', '本地 QQ 桥接服务无法连接或状态读取超时')
    if not isinstance(status, dict) or not isinstance(status.get('snowluma'), dict):
        return unavailable('status_unavailable', '未能取得完整连接状态，暂时无法确认是否可聊天')

    gateway = status['snowluma']
    if gateway.get('connected') is not True:
        return unavailable('gateway_disconnected', 'QQ 网关与桥接的连接已断开，正在等待重连')
    if gateway.get('accountOnline') is False:
        return unavailable('qq_offline', '小鱼的 QQ 账号当前不在线')
    if gateway.get('good') is False:
        return unavailable('qq_receive_unhealthy', 'QQ 网关报告消息接收链路异常，当前无法正常接收消息')
    if gateway.get('silent') is True:
        return unavailable('qq_heartbeat_missing', 'QQ 网关长时间没有心跳或消息，连接可能已失去响应')
    if status.get('mode') == 'reserved2':
        if status.get('socialV2Paused') is True:
            return unavailable('chat_paused', '聊天功能已暂停')
        if status.get('socialV2Enabled') is False:
            return unavailable('chat_disabled', '当前聊天模式已关闭')
    if status.get('roleMode') == 'silent':
        return unavailable('chat_silent', '当前处于静默模式，仅管理员私聊可以回复')
    if status.get('dshReady') is not True:
        return unavailable('model_service_unready', '本地聊天模型服务尚未就绪或无法连接')

    health = status.get('chatHealth')
    if not isinstance(health, dict) or health.get('streamConnected') is not True:
        return unavailable('reply_stream_disconnected', '聊天回复事件流尚未连接，正在等待恢复')

    recent = health.get('lastResult') or {}
    at = recent.get('at')
    if isinstance(at, (int, float)) and 0 < at < 1e14:
        try:
            out['last_request_at'] = datetime.fromtimestamp(at / 1000, timezone.utc).isoformat(timespec='seconds')
        except (ValueError, OSError, OverflowError):
            pass
    if recent.get('result') == 'error':
        code = recent.get('code')
        out['note'] = '这是最近一次真实聊天的失败结果，尚未观察到后续成功；未额外调用模型验证恢复。'
        return unavailable(code if code in REQUEST_REASONS else 'chat_error',
                           REQUEST_REASONS.get(code, REQUEST_REASONS['chat_error']))

    out.update(online=True, code='online', message='当前在线，可以聊天')
    if recent.get('result') == 'success':
        out['note'] = '本地连接正常，最近一次真实聊天已成功；未额外调用模型探测上游。'
    else:
        out['note'] = '本地连接正常，尚无已观察到的真实聊天结果；未额外调用模型验证上游。'
    return out
