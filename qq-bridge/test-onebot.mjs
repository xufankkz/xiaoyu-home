import assert from 'node:assert/strict';
import { once } from 'node:events';
import { WebSocketServer } from 'ws';
import { OneBotWebSocketClient } from './src/onebot-client.js';
const server = new WebSocketServer({host:'127.0.0.1',port:0});
await once(server,'listening');
let received=0;
server.on('connection', socket => {
 socket.on('message', raw => { const request=JSON.parse(raw); received++; socket.send(JSON.stringify({echo:request.echo,status:'ok',retcode:0,data:{user_id:123456789,nickname:'Synthetic test account'}})); });
});
const client=new OneBotWebSocketClient({url:`ws://127.0.0.1:${server.address().port}`,reconnect:false});
try {
 const opened=once(client,'open');client.connect();await opened;
 const full=await client.request('get_login_info');assert.equal(full.status,'ok');assert.equal(full.data.nickname,'Synthetic test account');
 assert.equal((await client.getLoginInfo()).user_id,123456789);
 const event=once(client,'private-message');for(const socket of server.clients) socket.send(JSON.stringify({post_type:'message',message_type:'private',user_id:123456789,message:[{type:'text',data:{text:'synthetic incoming event'}}]}));
 assert.equal((await event)[0].message[0].data.text,'synthetic incoming event');
 console.log(JSON.stringify({ok:true,requests:received,real_qq_messages:0}));
}finally {client.close();for(const socket of server.clients)socket.close();await new Promise(resolve=>server.close(resolve));}
