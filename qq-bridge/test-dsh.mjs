import {NodeApiClient,unwrap} from './src/dsh-client.js';
const client=new NodeApiClient(process.env.XIAOYU_DSH_BASE,20000,{token:process.env.XIAOYU_DSH_TOKEN});
const presets=unwrap(await client.callUnary('agentPresets/list',{}),'agentPresets/list');
const settings=unwrap(await client.callUnary('settings/describe',{}),'settings/describe');
const serialized=JSON.stringify(presets);if(!serialized.includes('qq-chat-v2'))throw new Error('Missing QQ preset');
const models=JSON.stringify(settings);if(!models.includes('xiaoyu-local'))throw new Error('Missing instance provider');
console.log(JSON.stringify({ok:true,presets_registered:true,provider_registered:true,paid_requests:0}));
