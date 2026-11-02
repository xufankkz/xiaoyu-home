/* Shared autonomous Douyin controls for browser and desktop. */
(function () {
  'use strict';
  async function request(path, body) {
    const response=await fetch('/api/douyin'+path,body===undefined?{cache:'no-store'}:{
      method:'POST',headers:{'Content-Type':'application/json','X-Xiaoyu-Settings':'1'},body:JSON.stringify(body)
    });
    const result=await response.json();
    if(!response.ok||!result.ok)throw new Error(result.error||'抖音操作未完成');
    return result.data;
  }
  const states={idle:'休息中',connecting:'核对账号',searching:'寻找想看的内容',watching:'正在观看',paused:'已暂停'};
  const statuses={watched:'已看过',ready:'未发布',not_published:'未发布',draft:'未发布（旧记录）',pending:'未发布（旧记录）',publishing:'正在发布',published:'已发布',uncertain:'送达待核对',discarded:'已关闭'};
  const note=text=>'<p class="tiny muted">'+esc(text)+'</p>';
  const field=(key,label,value,type='text',extra='')=>'<label class="room-input"><span>'+esc(label)+'</span><input data-douyin-setting="'+key+'" type="'+type+'" value="'+esc(value)+'" '+extra+'></label>';
  const check=(key,label,value)=>'<label class="dy-check"><input data-douyin-setting="'+key+'" type="checkbox" '+(value?'checked':'')+'><span>'+esc(label)+'</span></label>';
  const textarea=(key,label,value)=>'<label class="room-input dy-wide"><span>'+esc(label)+'</span><textarea data-douyin-setting="'+key+'" rows="3" maxlength="1800">'+esc(value)+'</textarea></label>';
  function source(row){
    return /^https:\/\/www\.douyin\.com\/video\/\d+$/.test(row.source||'')?'<p class="tiny"><a href="'+esc(row.source)+'" target="_blank" rel="noopener noreferrer">打开原作品</a></p>':'';
  }
  async function mount(root){
    const token=app.token;
    let data,timer,busy=false,dirty=false,settingsOpen=false,query='';
    const active=()=>root.isConnected&&app.token===token;
    const alive=()=>{if(active())timer=setTimeout(poll,5000);};
    root.innerHTML='<div class="page-head"><h1 class="page-title">小鱼的抖音日常</h1><p class="page-sub">自己找喜欢的内容，自主表达，把有意思的见闻慢慢记住。</p></div><div data-dy-host class="dy-layout"></div>';
    const host=root.querySelector('[data-dy-host]');host.innerHTML=skeleton(4);
    app.leave=()=>!busy&&(!dirty||confirm('抖音设置尚未保存，要放弃这些修改吗？'));
    const unload=event=>{if(dirty||busy){event.preventDefault();event.returnValue='';}};
    window.addEventListener('beforeunload',unload);
    onDispose(()=>{clearTimeout(timer);window.removeEventListener('beforeunload',unload);});
    function render(){
      const c=data.config,today=data.today||{},receipts=data.receipts||[];
      host.innerHTML='<section class="card dy-control"><div class="room-actions"><h2>今天在看什么</h2>'+pill(states[data.status]||data.status,data.running?'primary':'')+'</div>'+
        '<p role="status" id="dyStatus">'+esc(data.message)+'</p>'+
        '<div class="dy-stats"><span><strong>'+Number(today.calls||0)+'</strong> / '+c.daily_calls+' 次理解</span><span><strong>'+Number(today.tokens||0).toLocaleString()+'</strong> / '+Number(c.daily_tokens).toLocaleString()+' tokens</span><span><strong>'+Number(today.comments||0)+'</strong> / '+c.daily_comments+' 条评论</span><span><strong>'+Number(today.mentions||0)+'</strong> / '+c.daily_mentions+' 次 @ 爸爸</span></div>'+
        note('供应商用量 credit：'+Number(today.credit||0).toFixed(4)+'；按接口原值显示，不换算成人民币。请求结果未知时保留预算预留。')+
        '<div class="room-actions"><button class="btn primary" id="dyBrowse" '+(data.running||!c.enabled?'disabled':'')+'>开始刷抖音</button><button class="btn" id="dyConnect" '+(data.running?'disabled':'')+'>核对桌面账号</button><button class="btn" id="dyStart" '+(data.running||!c.enabled?'disabled':'')+'>让小鱼自己找着看</button><button class="btn" id="dyShare" '+(data.running||!c.enabled?'disabled':'')+'>找一条 @ 爸爸</button><button class="btn" id="dyPreview" '+(data.running||!c.enabled?'disabled':'')+'>只看一条，不评论</button><button class="btn" id="dyStop">暂停观看和定时任务</button></div>'+
        '<div class="dy-query"><input id="dyQuery" maxlength="60" value="'+esc(query)+'" aria-label="这轮想看的内容" placeholder="也可以指定这轮主题，例如：卡通画"><button class="btn" id="dySearch" '+(data.running||!c.enabled?'disabled':'')+'>按这个主题看</button></div>'+
        note('今日观看 '+(Number(today.seconds||0)/60).toFixed(1)+' / '+c.daily_minutes+' 分钟。评论由小鱼自主决定，直接发布。定时观看：'+(data.schedule_paused?'已手动暂停':c.auto_schedule?'已开启，每 '+c.interval_minutes+' 分钟检查一次':'未开启')+'。安静时段 '+c.quiet_start+'–'+c.quiet_end+'（电脑本地时间）。QQ 有新消息时优先回到聊天。')+
        note('想明确邀请爸爸看一条内容，可以点“找一条 @ 爸爸”。观看每条复制一次作品链接，发布前再核对一次。')+
        note(c.community_enabled?'自主选题会每三轮安排一轮看看大肥鱼和其他小鱼，首次先看同类小鱼，其余轮次按自己的兴趣选择。':'目前按自己的日常兴趣选题；可在“每天的安排”开启同类小鱼搜索。')+
        note('点“开始刷抖音”可立即发起一轮观看；填写主题就按主题看，留空由她自己选。如未自动切换，请在 15 秒内把抖音切到前台。')+
        '<p class="dy-result" id="dyResult" role="status"></p></section>'+
        '<section class="card dy-connection"><h2>她的账号与观看范围</h2><p>'+esc(c.self_name)+' · '+esc(c.self_id)+'</p><p>爸爸：'+esc(c.father_name)+' · '+esc(c.father_id)+'</p>'+note(data.connection.account_checked?'上次核对：'+data.connection.checked_at:'尚未核对本次桌面连接')+
        note('仅使用已登录的桌面抖音。不新增好友，不自动关注、点赞或私信。只有已绑定的爸爸可以被真实提及。')+note(data.audio)+
        note('自动观看需要 Windows 保持解锁、抖音窗口位于前台；你切换窗口、登录失效或页面无法核对时会暂停。')+
        (data.frame_at?'<details class="dy-frame"><summary>最近一次抽样画面 · '+esc(new Date(data.frame_at*1000).toLocaleTimeString())+'</summary><img alt="最近抽样的抖音页面" src="/api/douyin/frame?t='+data.frame_at+'" loading="lazy"></details>':'')+'</section>'+
        '<section class="card dy-wide"><h2>每天的安排</h2><form id="dyQuickForm"><div class="dy-settings">'+
        check('enabled','启用抖音功能',c.enabled)+check('auto_schedule','按间隔自主观看（抖音在前台时）',c.auto_schedule)+check('community_enabled','也看看大肥鱼和其他小鱼',c.community_enabled)+
        field('daily_minutes','每天最多看多久（分钟）',c.daily_minutes,'number','min="1" max="1440"')+
        field('session_minutes','每轮最多看多久（分钟）',c.session_minutes,'number','min="1" max="120"')+
        field('daily_comments','每天最多评论几次',c.daily_comments,'number','min="0" max="10"')+
        field('daily_mentions','每天最多 @ 爸爸几次',c.daily_mentions,'number','min="0" max="5"')+
        field('per_video_comments','每条作品最多评论几次',c.per_video_comments,'number','min="1" max="2"')+
        field('interval_minutes','两轮之间隔多久（分钟）',c.interval_minutes,'number','min="30" max="1440"')+
        field('quiet_start','安静时段开始',c.quiet_start,'time')+field('quiet_end','安静时段结束',c.quiet_end,'time')+
        '</div>'+note('评论次数与 @ 次数设为 0 可关闭相应操作；修改后点击保存，两端共用。当前轮进行中时，请先暂停再修改。')+
        '<div class="room-actions"><button class="btn primary" type="submit" data-dy-save id="dyQuickSave" disabled>保存每天的安排</button><span data-dy-save-result role="status"></span></div></form></section>'+
        (receipts.length?'<section class="card dy-wide"><h2>发送结果</h2>'+note('这些评论已经尝试发送，但客户端没有明确回执。核对真实结果后可关闭记录，不会自动重发。')+
          receipts.map(r=>'<article class="dy-record" data-receipt-id="'+esc(r.id)+'"><strong>'+esc(r.title||'一条交流')+'</strong><p>'+esc(r.comment||'')+'</p>'+note(r.reason||'')+source(r)+
          '<div class="room-actions"><button class="btn" data-receipt="confirm_published" '+(data.running?'disabled':'')+'>我已在抖音确认发出</button><button class="btn" data-receipt="confirm_not_published" '+(data.running?'disabled':'')+'>我已核对未发出，关闭这次尝试</button></div></article>').join('')+'</section>':'')+
        '<section class="card dy-wide"><div class="room-actions"><h2>今天留下的见闻</h2><button class="btn sm" id="dyMemory">补记尚未同步的记忆</button></div>'+note('今天记下 '+Number(data.digest?.watched||0)+' 条见闻，确认发布 '+Number(data.digest?.published||0)+' 条评论。日结来自已保存记录，不额外调用模型。')+
        ((data.digest?.interests||[]).map(s=>'<p>'+esc(s)+'</p>').join('')||note('有了新的观看感受后，会在这里慢慢积累。'))+'</section>'+
        '<section class="card dy-wide"><h2>观看与交流记录</h2>'+note('显示最近 80 条；原始历史在本地保留。以前未发送的记录继续保留，不会集中补发。')+
        (data.records.length?'<div class="dy-records">'+data.records.map(r=>'<article class="dy-record"><div class="room-actions"><strong>'+esc(r.title||'一条见闻')+'</strong>'+pill(statuses[r.status]||r.status)+'<time class="tiny muted">'+esc(r.at)+'</time></div><p>'+esc(r.summary||'')+'</p>'+
          (r.interest?note('这次的感受：'+r.interest):'')+(r.comment?'<p class="dy-quote">'+esc(r.status==='published'?'实际评论：':'未发布的想法：')+esc((r.mention_father==='yes'?'@'+c.father_name+' ':'')+r.comment)+'</p>':'')+
          note(r.reason||'')+source(r)+note(r.memory==='recorded'?'已保存到小鱼的抖音见闻记忆':r.memory==='pending'?'记忆等待同步':'')+'</article>').join('')+'</div>':note('第一次观看后，这里会出现她看到的内容和自己的感受。'))+'</section>'+
        '<details class="card dy-wide" id="dySettings" '+(settingsOpen?'open':'')+'><summary>兴趣与公开表达</summary><form id="dyForm"><div class="dy-settings">'+
        textarea('interests','起始兴趣；她也会参考自己最近的观看感受',c.interests)+textarea('community_topics','想了解的大肥鱼和小鱼群体',c.community_topics)+textarea('public_persona','公开场合使用的角色资料',c.public_persona)+
        '</div>'+note('公开角色资料用于抖音交流，原有聊天人设及私人记忆保持。')+
        '<div class="room-actions"><button class="btn primary" type="submit" data-dy-save id="dySave" disabled>保存兴趣与公开表达</button><span data-dy-save-result role="status"></span></div></form>'+
        '<a class="settings-link" id="dyModelSettingsLink" href="#/settings?tab=models&section=douyin">抖音专用模型、Key 与用量上限 → 设置</a></details>';
      bind();
    }
    async function action(path,body){
      if(busy)return;
      if(dirty){host.querySelector('#dyResult').textContent='请先保存正在编辑的设置。';return;}
      busy=true;host.querySelector('#dyResult').textContent='正在处理…';
      try{await request(path,body);if(!active())return;data=await request('');render();toast('抖音状态已更新');}
      catch(e){if(active())host.querySelector('#dyResult').textContent=e.message;}
      finally{busy=false;}
    }
    function bind(){
      const q=s=>host.querySelector(s);
      q('#dyQuery').oninput=e=>{query=e.target.value;};
      q('#dyConnect').onclick=()=>action('/connect',{});
      q('#dyBrowse').onclick=()=>action('/start',{manual:true,query:query.trim()});
      q('#dyStart').onclick=()=>action('/start',{});
      q('#dyShare').onclick=()=>action('/start',{query,share_father:true});
      q('#dyPreview').onclick=()=>action('/start',{query,preview:true});
      q('#dySearch').onclick=()=>{if(!query.trim()){q('#dyResult').textContent='请填写这轮主题。';return;}action('/start',{query:query.trim()});};
      q('#dyStop').onclick=async()=>{try{await request('/stop',{});toast('已请求暂停，定时观看也已暂停');}catch(e){toast(e.message);}};
      q('#dyMemory').onclick=()=>action('/memory',{});
      q('#dySettings').ontoggle=e=>{settingsOpen=e.currentTarget.open;};
      host.querySelectorAll('[data-douyin-setting]').forEach(e=>e.oninput=()=>{
        dirty=true;host.querySelectorAll('[data-dy-save]').forEach(b=>{b.disabled=data.running;});
      });
      async function save(e){
        e.preventDefault();if(busy||data.running)return;
        const changes={};host.querySelectorAll('[data-douyin-setting]').forEach(f=>{
          const value=f.type==='checkbox'?f.checked:f.type==='number'?Number(f.value):f.value;
          if(value!==data.config[f.dataset.douyinSetting])changes[f.dataset.douyinSetting]=value;
        });
        if(!Object.keys(changes).length){dirty=false;return;}
        busy=true;host.querySelectorAll('[data-dy-save]').forEach(b=>{b.disabled=true;});
        try{data=await request('/settings',{revision:data.revision,changes});if(active()){dirty=false;render();toast('抖音设置已保存，两端共用');}}
        catch(err){if(active()){host.querySelectorAll('[data-dy-save-result]').forEach(s=>{s.textContent=err.message;});host.querySelectorAll('[data-dy-save]').forEach(b=>{b.disabled=false;});}}
        finally{busy=false;}
      }
      q('#dyQuickForm').onsubmit=save;q('#dyForm').onsubmit=save;
      host.querySelectorAll('[data-receipt]').forEach(b=>b.onclick=()=>{
        if(!confirm('请确认已在抖音客户端核对真实结果。此操作只记录送达结果，不会重新发送。'))return;
        action('/receipt',{id:b.closest('[data-receipt-id]').dataset.receiptId,action:b.dataset.receipt,confirmed:true});
      });
    }
    async function poll(){
      try{const fresh=await request('');if(!active())return;if(!dirty&&!busy&&!host.querySelector('textarea:focus,input:focus')){data=fresh;render();}}
      catch(e){if(active())host.querySelector('#dyResult').textContent=e.message;}
      alive();
    }
    try{data=await request('');if(active()){render();alive();}}catch(e){if(active())host.innerHTML='<section class="card">'+note(e.message)+'</section>';}
  }
  function mountModelSettings(root, {notify=()=>{},canSave=()=>true}={}){
    let data=null,busy=false,disposed=false,timer;
    const keys=['endpoint','model','daily_calls','daily_tokens'];
    const active=()=>!disposed&&root.isConnected;
    const controls=()=>Array.from(root.querySelectorAll('[data-douyin-model-setting]'));
    const value=f=>f.type==='number'?Number(f.value):f.value;
    const configDirty=()=>!!data&&controls().some(f=>value(f)!==data.config[f.dataset.douyinModelSetting]);
    const isDirty=()=>configDirty()||!!root.querySelector('#settingsDouyinKey')?.value;
    const output=text=>{const status=root.querySelector('#settingsDouyinResult');if(status)status.textContent=text;};
    function mark(){
      const save=root.querySelector('#settingsDouyinSave'),keySave=root.querySelector('#settingsDouyinKeySave');
      if(save)save.disabled=busy||!data||data.running||!configDirty();
      if(keySave)keySave.disabled=busy||!data||data.running||configDirty()||!root.querySelector('#settingsDouyinKey').value.trim();
      notify();
    }
    function render(){
      const c=data.config,today=data.today||{};
      const input=(key,label,hint,type,extra='')=>'<label class="setting-row"><span class="setting-label"><strong>'+esc(label)+'</strong><span>'+esc(hint)+'</span></span><span class="setting-control"><input form="settingsDouyinForm" data-douyin-model-setting="'+key+'" aria-label="'+esc(label)+'" type="'+type+'" value="'+esc(c[key])+'" '+extra+' '+(data.running?'disabled':'')+'></span></label>';
      root.innerHTML='<p class="settings-info">'+esc(data.running?'抖音正在观看，请先到抖音页暂停，再修改模型配置。':'仅用于抖音观看与交流。保存配置不会开始观看或调用模型。')+' 今日 '+Number(today.calls||0)+' / '+c.daily_calls+' 次请求，'+Number(today.tokens||0).toLocaleString()+' / '+Number(c.daily_tokens).toLocaleString()+' tokens。</p>'+
        input('endpoint','抖音专用接口地址','填写专用模型的 HTTPS 接口根地址。','url','required')+
        input('model','抖音专用模型','使用服务商为当前 Key 开通的准确模型名。','text','required maxlength="500"')+
        input('daily_calls','每日模型请求上限','所有抖音观看轮次共用，不影响聊天与绘画。','number','min="1" max="300" step="1" required')+
        input('daily_tokens','每日 token 预算','请求前预留预算，达到上限会暂停；已用账目保留。','number','min="4000" max="50000000" step="1" required')+
        '<div class="settings-actions"><button type="button" class="btn primary" id="settingsDouyinSave" disabled>保存抖音模型设置</button><button type="button" class="btn" id="settingsDouyinReload">重新读取抖音配置</button><a class="settings-link" href="#/douyin">返回抖音日常 →</a></div><p class="tiny" id="settingsDouyinResult" role="status"></p>'+
        '<details class="settings-key"><summary>更换抖音专用 Key · '+(data.key_configured?'已配置，不回显':'尚未配置')+'</summary><p class="tiny muted">仅更新抖音专用 Key。请先保存上面的模型设置，留空不修改。</p><input form="settingsDouyinForm" type="password" id="settingsDouyinKey" aria-label="抖音专用 Key" autocomplete="new-password" spellcheck="false" placeholder="需要更换时填写新 Key" '+(data.running?'disabled':'')+'><button type="button" class="btn" id="settingsDouyinKeySave" disabled>保存抖音 Key</button></details>';
      controls().forEach(f=>f.oninput=()=>{output('尚未保存');mark();});
      root.querySelector('#settingsDouyinKey').oninput=()=>{output('');mark();};
      root.querySelector('#settingsDouyinSave').onclick=save;
      root.querySelector('#settingsDouyinKeySave').onclick=saveKey;
      root.querySelector('#settingsDouyinReload').onclick=()=>{
        if(busy||isDirty()&&!confirm('抖音模型设置尚未保存，要放弃这些修改并重新读取吗？'))return;
        read();
      };
      root.onkeydown=e=>{if(e.key==='Enter'&&e.target.matches('input')){e.preventDefault();if(e.target.id==='settingsDouyinKey')saveKey();else save();}};
      mark();
    }
    async function read(){
      if(busy)return;busy=true;mark();
      try{const fresh=await request('');if(active()){data=fresh;render();}}
      catch(e){if(active()){
        if(!data){root.innerHTML='<p id="settingsDouyinResult" role="status"></p><button type="button" class="btn" id="settingsDouyinRetry">重新读取抖音配置</button>';root.querySelector('#settingsDouyinRetry').onclick=read;}
        output(e.message);
      }}
      finally{busy=false;if(active())mark();}
    }
    async function save(){
      if(busy||!data||data.running||!configDirty())return;
      if(!canSave()){output('请先保存设置页的其他修改。');return;}
      if(!controls().every(f=>f.reportValidity()))return;
      const changes={};controls().forEach(f=>{const key=f.dataset.douyinModelSetting;if(keys.includes(key)&&value(f)!==data.config[key])changes[key]=value(f);});
      busy=true;mark();output('正在备份并保存抖音模型设置…');
      try{const fresh=await request('/settings',{revision:data.revision,changes});if(active()){const pendingKey=root.querySelector('#settingsDouyinKey').value;data=fresh;render();root.querySelector('#settingsDouyinKey').value=pendingKey;output('抖音模型设置已保存，网页与软件共用。');}}
      catch(e){if(active())output(e.message);}
      finally{busy=false;if(active())mark();}
    }
    async function saveKey(){
      const field=root.querySelector('#settingsDouyinKey'),api_key=field?.value.trim();
      if(busy||!data||data.running||!api_key)return;
      if(configDirty()||!canSave()){output('请先保存其他修改，再更换抖音 Key。');return;}
      busy=true;mark();output('正在备份并保存抖音专用 Key…');
      try{const fresh=await request('/key',{revision:data.revision,api_key});if(active()){field.value='';data=fresh;render();output('抖音专用 Key 已更新。');}}
      catch(e){if(active())output(e.message);}
      finally{busy=false;if(active())mark();}
    }
    root.innerHTML='<p class="muted">正在读取抖音专用模型配置…</p>';
    read();
    const poll=async()=>{if(!active())return;if(!busy&&!isDirty()&&!root.querySelector('input:focus'))await read();if(active())timer=setTimeout(poll,15000);};
    timer=setTimeout(poll,15000);
    return {isDirty,isBusy:()=>busy,dispose(){disposed=true;clearTimeout(timer);}};
  }
  window.XiaoyuDouyin={mount,mountModelSettings};
})();
