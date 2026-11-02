// Independent OneBot v11 forward WebSocket client, using its public protocol.
import { EventEmitter } from 'node:events';
import { randomUUID } from 'node:crypto';
import WebSocket from 'ws';
export const text = value => [{ type: 'text', data: { text: String(value) } }];
export class OneBotWebSocketClient extends EventEmitter {
  constructor({ url, accessToken, reconnect = true, requestTimeoutMs = 15000 }) {
    super();
    Object.assign(this, { url, accessToken, reconnect, requestTimeoutMs });
    this.pending = new Map(); this.closed = false; this.retry = null; this.socket = null;
  }
  onEvent(handler) { return this.on('event', handler); }
  onPrivateMessage(handler) { return this.on('private-message', handler); }
  onGroupMessage(handler) { return this.on('group-message', handler); }
  onNotice(type, handler) { return this.on('notice:' + type, handler); }
  onBotStatus(handler) { return this.on('notice:bot_status', handler); }
  connect() {
    this.closed = false; clearTimeout(this.retry);
    if (this.socket && this.socket.readyState < WebSocket.CLOSING) return;
    const target = new URL(this.url);
    if (!['ws:', 'wss:'].includes(target.protocol) || target.username || target.password) throw new Error('WebSocket 地址无效');
    this.socket = new WebSocket(target, { headers: this.accessToken ? { Authorization: 'Bearer ' + this.accessToken } : {} });
    const socket = this.socket;
    socket.on('open', () => this.emit('open'));
    socket.on('message', raw => {
      let event; try { event = JSON.parse(raw.toString()); } catch { return; }
      if (event.echo && this.pending.has(event.echo)) {
        const pending = this.pending.get(event.echo); clearTimeout(pending.timer); this.pending.delete(event.echo);
        if (event.status === 'ok' && Number(event.retcode || 0) === 0) pending.resolve(event);
        else pending.reject(new Error('连接端未执行动作，代码 ' + String(event.retcode)));
        return;
      }
      this.emit('event', event);
      if (event.post_type === 'message') this.emit(event.message_type + '-message', event);
      if (event.post_type === 'notice') this.emit('notice:' + event.notice_type, event);
    });
    socket.on('error', error => { if (this.listenerCount('error')) this.emit('error', error); });
    socket.on('close', (code, reason) => {
      this.emit('close', code, reason);
      for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(new Error('QQ 连接已断开')); }
      this.pending.clear();
      if (!this.closed && this.reconnect) this.retry = setTimeout(() => this.connect(), 3000);
    });
  }
  close(code = 1000, reason = 'client-close') {
    this.closed = true; clearTimeout(this.retry); this.socket?.close(code, reason);
  }
  request(action, params = {}) {
    return new Promise((resolve, reject) => {
      if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return reject(new Error('QQ 接口未连接'));
      const echo = randomUUID();
      const timer = setTimeout(() => { this.pending.delete(echo); reject(new Error('连接端动作等待超时，送达状态未知')); }, this.requestTimeoutMs);
      this.pending.set(echo, { resolve, reject, timer });
      this.socket.send(JSON.stringify({ action, params, echo }), error => {
        if (!error) return;
        clearTimeout(timer); this.pending.delete(echo); reject(error);
      });
    });
  }
  async raw(action, params) { return (await this.request(action, params)).data; }
  getLoginInfo() { return this.raw('get_login_info'); }
  getMessage(messageId) { return this.raw('get_msg', typeof messageId === 'object' ? messageId : { message_id: messageId }); }
  getImage(params) { return this.raw('get_image', typeof params === 'object' ? params : { file: params }); }
  getGroupMemberInfo(groupId, userId, noCache = false) {
    return this.raw('get_group_member_info', typeof groupId === 'object' ? groupId : { group_id: groupId, user_id: userId, no_cache: noCache });
  }
  getGroupMemberList(groupId) { return this.raw('get_group_member_list', typeof groupId === 'object' ? groupId : { group_id: groupId }); }
  sendPrivateMessage(userId, message) { return this.raw('send_private_msg', { user_id: userId, message }); }
  sendGroupMessage(groupId, message) { return this.raw('send_group_msg', { group_id: groupId, message }); }
  fetchFaceEntity(id) { return this.raw('fetch_custom_face_detail', { emoji_id: id }); }
}
