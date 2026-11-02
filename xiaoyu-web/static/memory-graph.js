/* 小鱼的记忆星图：真实记录与概念关联，按需绘制的 Canvas 空间视图。 */
'use strict';

function memoryConstellationMarkup() {
  return '<div class="memory-observatory">' +
    '<div class="atlas-heading"><div><span class="atlas-eyebrow">XIAOYU · MEMORY ATLAS</span><h3>小鱼的记忆宇宙</h3><p>那些记住的事，在这里慢慢连成星座。</p></div>' +
      '<button class="atlas-button atlas-expand" id="gExpand" aria-pressed="false" aria-label="展开星图">⛶ <span>展开</span></button></div>' +
    '<div class="atlas-body">' +
      '<aside class="atlas-overview"><h4>星图导航 <span>OVERVIEW</span></h4><div class="atlas-minimap" id="gMap" aria-label="记忆分区导航"></div>' +
        '<div class="atlas-section-caption">记忆构成</div><div id="gGroups" class="atlas-groups"></div><p class="atlas-note">按类型与共享概念排布<br>每颗主星都对应一条真实记录</p></aside>' +
      '<div class="atlas-viewport"><canvas class="graph-canvas" id="gCanvas" tabindex="0" role="img" aria-label="立体记忆星图。拖动旋转，Shift 加拖动平移，滚轮缩放；点击星点预览，回车查看完整记录。"></canvas>' +
        '<div class="atlas-canvas-hint">拖动旋转 <span>·</span> 滚轮缩放 <span>·</span> 点击星点</div>' +
        '<div class="atlas-view-status" id="gViewStatus" aria-live="off">立体视角 · 100%</div></div>' +
      '<aside class="atlas-detail"><div class="atlas-detail-caption">记忆拾光 <span>MEMORY</span></div><div class="atlas-detail-body" id="gPreview" aria-live="polite"></div></aside>' +
    '</div><div class="atlas-toolbar">' +
      '<div class="atlas-controls"><button class="atlas-button" id="gRotate" aria-pressed="false">自动巡游</button>' +
        '<button class="atlas-button" id="gPlan" aria-pressed="false">俯视</button>' +
        '<button class="atlas-button" id="gLabels" aria-pressed="true">标签</button>' +
        '<button class="atlas-button" id="gLinks" aria-pressed="true">连线</button>' +
        '<button class="atlas-button atlas-square" id="gZoomOut" aria-label="缩小星图">−</button>' +
        '<button class="atlas-button atlas-square" id="gZoomIn" aria-label="放大星图">＋</button>' +
        '<button class="atlas-button" id="gReset">重置视图</button><button class="atlas-button" id="gRelayout">重新布局</button></div>' +
      '<div class="atlas-search"><input id="gSearch" type="search" autocomplete="off" aria-label="搜索星图中的记忆" placeholder="找一颗记忆…"><div id="gResults" class="atlas-results" hidden></div></div>' +
    '</div><div class="atlas-footer"><span id="gSearchStatus" role="status">星图已就绪</span><span>Shift + 拖动平移 · 双击星点查看原文</span></div></div>';
}

function createMemoryConstellation(canvas, data, onPick) {
  const host = canvas.closest('.memory-observatory');
  const find = (s) => host.querySelector(s);
  const safe = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number = (v, fallback = 0) => Number.isFinite(Number(v)) ? Number(v) : fallback;
  const limit = (v, lo, hi) => Math.min(hi, Math.max(lo, number(v)));
  const short = (v, length) => Array.from(String(v || '')).slice(0, length).join('') + (Array.from(String(v || '')).length > length ? '…' : '');
  const kinds = [
    {id:'long1', name:'1号长期记忆', color:'#9b6f2e', rgb:'155,111,46'},
    {id:'long2', name:'2号长期记忆', color:'#7c68ac', rgb:'124,104,172'},
    {id:'instant', name:'瞬时记忆', color:'#347590', rgb:'52,117,144'},
    {id:'short', name:'短期记忆', color:'#3f806e', rgb:'63,128,110'},
    {id:'language', name:'语言记忆', color:'#a36748', rgb:'163,103,72'},
    {id:'emotion', name:'情绪记忆', color:'#a35f80', rgb:'163,95,128'},
    {id:'knowledge', name:'学习知识', color:'#5b7c40', rgb:'91,124,64'},
    {id:'other', name:'其他记忆', color:'#61738d', rgb:'97,115,141'},
  ];
  const typeOf = (n) => n.kind === 'knowledge' ? 'knowledge' : (kinds.some(k => k.id === n.layer) ? n.layer : 'other');
  const nodes = (data.nodes || []).map((n, i) => ({...n, index:i, type:typeOf(n), concepts:Array.isArray(n.concepts) ? n.concepts.map(String) : []}));
  const byId = new Map(nodes.map(n => [n.id, n]));
  const edges = (data.edges || []).map(e => ({a:byId.get(e.source), b:byId.get(e.target), weight:number(e.weight, 1)})).filter(e => e.a && e.b && e.a !== e.b);
  const adjacency = new Map(nodes.map(n => [n.id, new Set()]));
  edges.forEach(e => {adjacency.get(e.a.id).add(e.b.id); adjacency.get(e.b.id).add(e.a.id);});
  const types = kinds.map(k => ({...k, nodes:nodes.filter(n => n.type === k.id)})).filter(k => k.nodes.length);
  const ignored = new Set(['user','assistant','小鱼','大肥鱼','记忆','记录','开心','平静','好的','哈哈','不用','我这','你这','我不','你不','这个','那个','什么','没有','就是','一个','今天','现在','还是','自己','知道','感觉','可以']);
  const clusters = [];
  types.forEach(type => {
    const frequencies = new Map();
    type.nodes.forEach(n => new Set(n.concepts).forEach(c => {
      if (c.length >= 2 && c.length <= 8 && !ignored.has(c.toLowerCase())) frequencies.set(c, (frequencies.get(c) || 0) + 1);
    }));
    const topicCount = type.nodes.length > 180 ? 3 : type.nodes.length > 80 ? 2 : 0;
    const topics = [...frequencies].filter(([,count]) => count >= 5).sort((a,b) => b[1]-a[1] || a[0].localeCompare(b[0])).slice(0, topicCount).map(([c]) => c);
    const buckets = new Map();
    type.nodes.forEach(n => {
      const topic = topics.find(t => n.concepts.includes(t)) || '';
      if (!buckets.has(topic)) buckets.set(topic, []);
      buckets.get(topic).push(n);
    });
    // 小主题并回本类，较大的星团按真实关联分成几组，避免挤成一个实心圆。
    for (const [topic,members] of [...buckets]) {
      if(topic && members.length<6) {
        if(!buckets.has('')) buckets.set('',[]);
        buckets.get('').push(...members);buckets.delete(topic);
      }
    }
    buckets.forEach((members, topic) => {
      const partCount=Math.ceil(members.length/100),remaining=new Set(members.map(n=>n.id));
      const ordered=members.slice().sort((a,b)=>adjacency.get(b.id).size-adjacency.get(a.id).size || String(a.id).localeCompare(String(b.id)));
      for(let part=0;part<partCount;part++) {
        const size=Math.ceil(remaining.size/(partCount-part)),partNodes=[],queue=[];
        while(partNodes.length<size && remaining.size) {
          if(!queue.length) queue.push(ordered.find(n=>remaining.has(n.id)).id);
          const id=queue.shift();if(!remaining.has(id)) continue;
          remaining.delete(id);partNodes.push(byId.get(id));
          adjacency.get(id).forEach(neighbor=>{if(remaining.has(neighbor)) queue.push(neighbor);});
        }
        const title=(topic || type.name)+(partCount>1?' · '+(part+1):'');
        const cluster={id:clusters.length,type,topic,title,name:type.name+(topic?' · '+topic:''),nodes:partNodes};
        partNodes.forEach(n=>{n.cluster=cluster;n.color=type.color;n.rgb=type.rgb;});
        clusters.push(cluster);
      }
    });
  });

  let width = 1, height = 1, dpr = 1, ctx = canvas.getContext('2d');
  let destroyed = false, frame = 0, pending = 0, autoFrame = 0, lastAuto = 0;
  let auto = false, showLabels = true, showLinks = true, flat = false, layout = 0;
  let selected = null, hover = null, activeType = '', searchTerm = '';
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const view = {yaw:-0.16, pitch:-0.12, zoom:1, panX:0, panY:0};
  const points = new Map();
  const pointers = new Map();
  let gesture = null;
  const listeners = [];
  const listen = (el, name, fn, opts) => {el.addEventListener(name, fn, opts); listeners.push(() => el.removeEventListener(name, fn, opts));};
  const random = (value) => {
    let hash = 2166136261;
    for (const c of String(value)) hash = Math.imul(hash ^ c.charCodeAt(0), 16777619);
    return (hash >>> 0) / 4294967296;
  };
  const dust = Array.from({length:100}, (_,i) => ({x:random('x'+i),y:random('y'+i),r:i%7===0 ? 1 : .55,alpha:.10+random('a'+i)*.18}));
  let palette = {};
  function updatePalette() {
    const css = getComputedStyle(host), dark = css.colorScheme === 'dark';
    const read = name => css.getPropertyValue('--canvas-' + name).trim();
    palette = Object.fromEntries(['center','mid','edge','muted','faint','rgb','tooltip','tooltip-ink','tooltip-line'].map(name => [name, read(name)]));
    const light = ['#9b6f2e','#7c68ac','#347590','#3f806e','#a36748','#a35f80','#5b7c40','#61738d'];
    const night = ['#e2bd7c','#b6a6e6','#84ccdf','#8bcbb4','#e3ad86','#e2a0c1','#b1cf8d','#a1b9d2'];
    kinds.forEach((kind, i) => { kind.color = (dark ? night : light)[i]; kind.rgb = kind.color.match(/\w\w/g).map(v => parseInt(v,16)).join(','); });
    types.forEach(type => Object.assign(type, {color:kinds.find(k=>k.id===type.id).color,rgb:kinds.find(k=>k.id===type.id).rgb}));
    nodes.forEach(node => {const kind=kinds.find(k=>k.id===node.type);node.color=kind.color;node.rgb=kind.rgb;});
    host.querySelectorAll('[data-type]').forEach(button => {
      const kind=kinds.find(k=>k.id===button.dataset.type);
      button.style.setProperty('--star-color',kind.color);
      if(button.querySelector('i')) button.querySelector('i').style.background=kind.color;
    });
    updatePreview(); requestDraw();
  }

  function arrange() {
    const main = clusters.find(c => c.type.id === 'long1');
    const outer = clusters.filter(c => c !== main);
    if (main) {main.x=0; main.y=-.04; main.z=.45; main.radius=.19;}
    outer.forEach((c, i) => {
      const angle = -Math.PI/2 + i * Math.PI*2/Math.max(outer.length,1) + layout*.32;
      c.x=Math.cos(angle)*1.15;
      c.y=Math.sin(angle)*.84;
      c.z=Math.sin(angle*2 + .4)*.30;
      c.radius=Math.min(.29, .15 + Math.sqrt(c.nodes.length)*.014);
    });
    clusters.forEach(c => c.nodes.forEach((n,i) => {
      const y=1-2*(i+.5)/c.nodes.length;
      const theta=i*2.399963229728653 + layout*.8;
      const r=c.radius*(.45+random(n.id)*.55);
      const ring=Math.sqrt(Math.max(0,1-y*y));
      n.x=c.x+Math.cos(theta)*ring*r;
      n.y=c.y+y*r*.85;
      n.z=c.z+Math.sin(theta)*ring*r;
      n.r=1.1+limit(n.weight,.15,1)*1.6;
    }));
  }

  function projection(p) {
    const cy=Math.cos(view.yaw),sy=Math.sin(view.yaw),cp=Math.cos(view.pitch),sp=Math.sin(view.pitch);
    const x=p.x*cy-p.z*sy, z=p.x*sy+p.z*cy;
    const y=p.y*cp-z*sp, depth=p.y*sp+z*cp;
    const perspective=flat ? 1 : 1/(1+depth*.16);
    const scale=Math.min(width/3.35,height/2.7)*perspective*view.zoom;
    return {x:width/2+x*scale+view.panX, y:height/2+y*scale+view.panY, depth, scale, perspective};
  }

  function rgba(rgb, alpha) {return 'rgba('+rgb+','+alpha+')';}
  function nodeMatches(n) {
    return (!activeType || n.type === activeType) && (!searchTerm || (n.label+' '+n.concepts.join(' ')).toLocaleLowerCase().includes(searchTerm));
  }
  function orbit(c, radius, tilt) {
    ctx.beginPath();
    for (let i=0;i<=48;i++) {
      const a=i/48*Math.PI*2;
      const p=projection({x:c.x+Math.cos(a)*radius,y:c.y+Math.sin(a)*radius*Math.cos(tilt),z:c.z+Math.sin(a)*radius*Math.sin(tilt)});
      if (i===0) ctx.moveTo(p.x,p.y); else ctx.lineTo(p.x,p.y);
    }
    ctx.stroke();
  }

  function draw() {
    pending=0;
    if (destroyed) return;
    frame++;
    ctx.setTransform(dpr,0,0,dpr,0,0);
    ctx.clearRect(0,0,width,height);
    const bg=ctx.createRadialGradient(width*.5,height*.47,0,width*.5,height*.47,Math.max(width,height)*.72);
    bg.addColorStop(0,palette.center); bg.addColorStop(.65,palette.mid); bg.addColorStop(1,palette.edge);
    ctx.fillStyle=bg; ctx.fillRect(0,0,width,height);
    dust.forEach(s => {ctx.fillStyle=rgba(palette.rgb,s.alpha);ctx.beginPath();ctx.arc(s.x*width,s.y*height,s.r,0,Math.PI*2);ctx.fill();});
    const emphasis=selected || hover;
    const neighbors=emphasis ? adjacency.get(emphasis.id) : null;
    clusters.forEach(c => {
      const faded=activeType && c.type.id!==activeType;
      ctx.strokeStyle=rgba(c.type.rgb,faded?.035:.17);ctx.lineWidth=.65;
      orbit(c,c.radius*1.35,.65);orbit(c,c.radius*1.15,1.45);
    });
    points.clear();
    nodes.forEach(n => {const p=projection(n);points.set(n.id,{...p,node:n,r:Math.max(1.1,n.r*view.zoom*p.perspective)});});
    if(showLinks) {
      const normal=[],highlight=[];
      edges.forEach(e => (emphasis && (e.a.id===emphasis.id || e.b.id===emphasis.id) ? highlight : normal).push(e));
      for (const local of [false,true]) {
        ctx.beginPath();
        normal.forEach(e => {
          if ((e.a.cluster===e.b.cluster)!==local) return;
          if ((activeType && e.a.type!==activeType && e.b.type!==activeType) || (searchTerm && !nodeMatches(e.a) && !nodeMatches(e.b))) return;
          const a=points.get(e.a.id),b=points.get(e.b.id);ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);
        });
        ctx.lineWidth=local?.55:.45;ctx.strokeStyle=rgba(palette.rgb,emphasis?.03:local?.16:.065);ctx.stroke();
      }
      if(highlight.length) {
        ctx.beginPath();highlight.forEach(e => {const a=points.get(e.a.id),b=points.get(e.b.id);ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);});
        ctx.strokeStyle=rgba(emphasis.rgb,.58);ctx.lineWidth=.95;ctx.stroke();
      }
    }
    [...points.values()].sort((a,b)=>b.depth-a.depth).forEach(p => {
      const n=p.node;
      if(p.x < -15 || p.y < -15 || p.x > width+15 || p.y > height+15) return;
      const dim=!nodeMatches(n) || (emphasis && n!==emphasis && !neighbors.has(n.id));
      const alpha=dim?.13:limit(.73-p.depth*.22,.4,1);
      const important=n===emphasis || (!dim && number(n.weight)>.8 && n.index%5===0);
      if(important) {
        const r=p.r*(n===emphasis?5:3.3),glow=ctx.createRadialGradient(p.x,p.y,0,p.x,p.y,r);
        glow.addColorStop(0,rgba(n.rgb,.15));glow.addColorStop(1,rgba(n.rgb,0));
        ctx.fillStyle=glow;ctx.fillRect(p.x-r,p.y-r,r*2,r*2);
      }
      ctx.beginPath();ctx.arc(p.x,p.y,p.r*(n===emphasis?1.3:1),0,Math.PI*2);ctx.fillStyle=rgba(n.rgb,alpha);ctx.fill();
      if(n===emphasis) {ctx.strokeStyle=rgba(n.rgb,.8);ctx.lineWidth=1;ctx.beginPath();ctx.arc(p.x,p.y,p.r+4,0,Math.PI*2);ctx.stroke();}
    });
    if(showLabels) {
      const labels=width<500?types.map(type=>{
        const anchor=clusters.filter(c=>c.type.id===type.id).sort((a,b)=>b.nodes.length-a.nodes.length)[0];
        return {...anchor,title:type.name,topic:'',nodes:type.nodes};
      }):clusters;
      const occupied=[];
      labels.slice().sort((a,b)=>b.nodes.length-a.nodes.length).forEach(c => {
        const p=projection({x:c.x,y:c.y-c.radius*1.5,z:c.z});
        const faded=activeType && activeType!==c.type.id;
        const label=c.title;
        ctx.textAlign='center';ctx.textBaseline='bottom';ctx.font='500 12px "Microsoft YaHei",sans-serif';
        const half=ctx.measureText(label).width/2;
        const x=limit(p.x,half+8,width-half-8);
        let y=limit(p.y,25,height-25),box=null;
        for(const offset of [0,-20,20,-40,40]) {
          const candidate={left:x-half-5,right:x+half+5,top:limit(y+offset,25,height-25)-14,bottom:limit(y+offset,25,height-25)+18};
          if(!occupied.some(b=>candidate.left<b.right && candidate.right>b.left && candidate.top<b.bottom && candidate.bottom>b.top)) {box=candidate;y=limit(y+offset,25,height-25);break;}
        }
        if(!box) return;
        occupied.push(box);
        ctx.fillStyle=rgba(c.type.rgb,faded?.18:.95);ctx.fillText(label,x,y);
        ctx.font='10px "Microsoft YaHei",sans-serif';ctx.fillStyle=faded?palette.faint:palette.muted;
        ctx.fillText((c.topic?c.type.name+' · ':'')+c.nodes.length+' 条',x,y+16);
      });
    }
    if(hover) {
      const p=points.get(hover.id),text=short(hover.label||'未命名记忆',20);
      ctx.font='12px "Microsoft YaHei",sans-serif';const w=Math.min(width-16,ctx.measureText(text).width+20);
      const x=limit(p.x+12,8,width-w-8),y=limit(p.y+15,8,height-32);
      ctx.fillStyle=palette.tooltip;ctx.fillRect(x,y,w,27);ctx.strokeStyle=palette['tooltip-line'];ctx.lineWidth=.7;ctx.strokeRect(x,y,w,27);
      ctx.textAlign='left';ctx.textBaseline='middle';ctx.fillStyle=palette['tooltip-ink'];ctx.fillText(text,x+10,y+14,w-16);
    }
    find('#gViewStatus').textContent=(flat?'俯视':'立体视角')+' · '+Math.round(view.zoom*100)+'%'+(auto?' · 巡游中':'');
  }
  function requestDraw() {if(!destroyed && !pending) pending=requestAnimationFrame(draw);}
  function resize() {
    const rect=canvas.getBoundingClientRect();width=Math.max(1,rect.width);height=Math.max(1,rect.height);
    dpr=Math.min(window.devicePixelRatio||1,1.75);
    const w=Math.round(width*dpr),h=Math.round(height*dpr);
    if(canvas.width!==w || canvas.height!==h) {canvas.width=w;canvas.height=h;}
    requestDraw();
  }
  function clearSearchResults() {find('#gResults').hidden=true;}
  function updatePreview() {
    const target=find('#gPreview');
    if(!selected) {
      target.innerHTML='<div class="atlas-empty-star" aria-hidden="true">✧</div><h4>从一颗记忆开始</h4><p>点一下星点，看看小鱼把什么留在了这里。</p><div class="atlas-totals"><span><b>'+nodes.length+'</b> 颗记忆</span><span><b>'+edges.length+'</b> 条关联</span></div><p class="atlas-small-note">大小表示显著度<br>连线来自记录的共享概念</p>';
      return;
    }
    const type=kinds.find(k=>k.id===selected.type);
    const related=adjacency.get(selected.id).size;
    target.innerHTML='<span class="atlas-memory-kind" style="--star-color:'+type.color+'">'+safe(type.name)+'</span>'+
      '<h4>'+safe(selected.label||'未命名记忆')+'</h4>'+
      '<div class="atlas-memory-date">'+safe(String(selected.t||'').replace('T',' ').slice(0,16)||'未记录时间')+'</div>'+
      '<div class="atlas-weight"><span>显著度</span><b>'+Math.round(limit(selected.weight,0,1)*100)+'%</b></div><div class="atlas-weight-bar"><i style="width:'+Math.round(limit(selected.weight,0,1)*100)+'%;background:'+type.color+'"></i></div>'+
      '<div class="atlas-concepts">'+selected.concepts.slice(0,5).map(c=>'<span>'+safe(c)+'</span>').join('')+'</div>'+
      '<p>'+related+' 条关联记忆</p><button class="atlas-button atlas-read" id="gRead">查看完整记录 ↗</button><button class="atlas-button atlas-clear" id="gClear">取消选中</button>';
    find('#gRead').onclick=()=>{if(selected && onPick) onPick(selected);};
    find('#gClear').onclick=()=>{selected=null;updatePreview();requestDraw();};
  }
  function selectNode(n,focus) {
    selected=n;hover=null;
    if(focus) {
      view.panX=0;view.panY=0;view.zoom=Math.max(view.zoom,1.65);
      const p=projection(n);view.panX=width/2-p.x;view.panY=height/2-p.y;
    }
    updatePreview();requestDraw();
  }
  function focusType(id) {
    activeType=activeType===id?'':id;
    find('#gGroups').querySelectorAll('[data-type]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.type===activeType)));
    find('#gMap').querySelectorAll('[data-type]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.type===activeType)));
    selected=null;hover=null;view.panX=0;view.panY=0;view.zoom=1;
    find('#gSearchStatus').textContent=activeType?'正在查看 '+kinds.find(k=>k.id===activeType).name+' · 再点一次显示全部':'显示全部记忆';
    updatePreview();requestDraw();
  }
  function resetView() {
    view.yaw=-.16;view.pitch=-.12;view.zoom=1;view.panX=0;view.panY=0;flat=false;
    selected=null;hover=null;activeType='';searchTerm='';find('#gSearch').value='';clearSearchResults();
    find('#gPlan').setAttribute('aria-pressed','false');
    find('#gGroups').querySelectorAll('[data-type]').forEach(b=>b.setAttribute('aria-pressed','false'));
    find('#gMap').querySelectorAll('[data-type]').forEach(b=>b.setAttribute('aria-pressed','false'));
    find('#gSearchStatus').textContent='视图已复位';updatePreview();requestDraw();
  }
  function relayout() {layout++;arrange();resetView();}
  function zoom(factor,x=width/2,y=height/2) {
    const old=view.zoom;view.zoom=limit(old*factor,.45,4);
    const ratio=view.zoom/old;
    view.panX=x-width/2-(x-width/2-view.panX)*ratio;
    view.panY=y-height/2-(y-height/2-view.panY)*ratio;
    requestDraw();
  }
  function visible() {return !document.hidden && canvas.getBoundingClientRect().bottom>0 && canvas.getBoundingClientRect().top<window.innerHeight;}
  function cruise(time) {
    autoFrame=0;
    if(destroyed || !auto || !visible()) return;
    if(time-lastAuto>=33) {view.yaw+=Math.min((time-lastAuto)/1000,.05)*.07;lastAuto=time;requestDraw();}
    autoFrame=requestAnimationFrame(cruise);
  }
  function syncCruise() {
    if(autoFrame) cancelAnimationFrame(autoFrame);autoFrame=0;lastAuto=performance.now();
    if(auto && visible()) autoFrame=requestAnimationFrame(cruise);
    requestDraw();
  }
  function setAutoRotate(value) {
    auto=Boolean(value);find('#gRotate').setAttribute('aria-pressed',String(auto));
    find('#gRotate').textContent=auto?'暂停巡游':'自动巡游';syncCruise();
  }
  function local(ev) {const rect=canvas.getBoundingClientRect();return {x:ev.clientX-rect.left,y:ev.clientY-rect.top};}
  function hit(p) {
    let best=null,bestDepth=Infinity;
    points.forEach(q=>{if(nodeMatches(q.node) && Math.hypot(q.x-p.x,q.y-p.y)<=Math.max(7,q.r+3) && q.depth<bestDepth) {best=q.node;bestDepth=q.depth;}});
    return best;
  }
  function onDown(ev) {
    if(ev.button>1 && ev.button!==2) return;
    ev.preventDefault();canvas.focus({preventScroll:true});canvas.setPointerCapture(ev.pointerId);
    const p=local(ev);pointers.set(ev.pointerId,p);hover=null;
    if(pointers.size===1) gesture={start:p,last:p,moved:false,pan:ev.shiftKey||ev.button!==0};
    if(pointers.size===2) {const [a,b]=[...pointers.values()];gesture={moved:true,distance:Math.hypot(a.x-b.x,a.y-b.y),mid:{x:(a.x+b.x)/2,y:(a.y+b.y)/2}};}
  }
  function onMove(ev) {
    const p=local(ev);
    if(pointers.has(ev.pointerId)) {
      pointers.set(ev.pointerId,p);
      if(pointers.size===2) {
        const [a,b]=[...pointers.values()],distance=Math.max(1,Math.hypot(a.x-b.x,a.y-b.y)),mid={x:(a.x+b.x)/2,y:(a.y+b.y)/2};
        zoom(distance/Math.max(gesture.distance,1),mid.x,mid.y);view.panX+=mid.x-gesture.mid.x;view.panY+=mid.y-gesture.mid.y;
        gesture={moved:true,distance,mid};
      } else if(gesture && gesture.last) {
        if(Math.hypot(p.x-gesture.start.x,p.y-gesture.start.y)>4) gesture.moved=true;
        if(gesture.moved) {
          const dx=p.x-gesture.last.x,dy=p.y-gesture.last.y;
          if(gesture.pan || flat) {view.panX+=dx;view.panY+=dy;} else {view.yaw+=dx*.006;view.pitch=limit(view.pitch+dy*.005,-1.15,1.15);}
          gesture.last=p;
        }
      }
      requestDraw();return;
    }
    const next=hit(p);if(next!==hover) {hover=next;canvas.style.cursor=next?'pointer':'grab';requestDraw();}
  }
  function onUp(ev) {
    if(!pointers.has(ev.pointerId)) return;
    const clicked=gesture && !gesture.moved && pointers.size===1;
    pointers.delete(ev.pointerId);
    if(canvas.hasPointerCapture(ev.pointerId)) canvas.releasePointerCapture(ev.pointerId);
    if(clicked) {const n=hit(local(ev));if(n) selectNode(n,false);else {selected=null;updatePreview();requestDraw();}}
    if(pointers.size===1) {const p=[...pointers.values()][0];gesture={start:p,last:p,moved:true,pan:false};} else gesture=null;
  }
  listen(canvas,'pointerdown',onDown);listen(canvas,'pointermove',onMove);listen(canvas,'pointerup',onUp);
  listen(canvas,'pointercancel',ev=>{pointers.delete(ev.pointerId);gesture=null;});
  listen(canvas,'pointerleave',()=>{if(!pointers.size && hover) {hover=null;requestDraw();}});
  listen(canvas,'contextmenu',ev=>ev.preventDefault());
  listen(canvas,'wheel',ev=>{ev.preventDefault();const p=local(ev);zoom(Math.exp(-ev.deltaY*.0013),p.x,p.y);},{passive:false});
  listen(canvas,'dblclick',ev=>{const n=hit(local(ev));if(n && onPick) onPick(n);});
  listen(canvas,'keydown',ev=>{
    if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','=','-','0','Enter'].includes(ev.key)) ev.preventDefault();
    if(ev.key==='ArrowLeft') view.yaw-=.08;
    else if(ev.key==='ArrowRight') view.yaw+=.08;
    else if(ev.key==='ArrowUp') view.pitch=limit(view.pitch-.08,-1.15,1.15);
    else if(ev.key==='ArrowDown') view.pitch=limit(view.pitch+.08,-1.15,1.15);
    else if(ev.key==='+' || ev.key==='=') zoom(1.2);
    else if(ev.key==='-') zoom(1/1.2);
    else if(ev.key==='0') resetView();
    else if(ev.key==='Enter' && selected && onPick) onPick(selected);
    requestDraw();
  });
  listen(find('#gRotate'),'click',()=>setAutoRotate(!auto));
  listen(find('#gPlan'),'click',()=>{flat=!flat;view.pitch=flat?0:-.12;view.yaw=flat?0:-.16;find('#gPlan').setAttribute('aria-pressed',String(flat));requestDraw();});
  listen(find('#gLabels'),'click',()=>{showLabels=!showLabels;find('#gLabels').setAttribute('aria-pressed',String(showLabels));requestDraw();});
  listen(find('#gLinks'),'click',()=>{showLinks=!showLinks;find('#gLinks').setAttribute('aria-pressed',String(showLinks));requestDraw();});
  listen(find('#gZoomIn'),'click',()=>zoom(1.2));listen(find('#gZoomOut'),'click',()=>zoom(1/1.2));
  listen(find('#gExpand'),'click',()=>{const expanded=host.classList.toggle('is-expanded');find('#gExpand').setAttribute('aria-pressed',String(expanded));find('#gExpand').setAttribute('aria-label',expanded?'收起星图':'展开星图');find('#gExpand').querySelector('span').textContent=expanded?'收起':'展开';resize();});
  listen(document,'keydown',ev=>{
    if(ev.key==='Escape' && !document.querySelector('#modalMask')) {
      if(!find('#gResults').hidden) clearSearchResults();
      else if(host.classList.contains('is-expanded')) find('#gExpand').click();
      else if(selected) {selected=null;updatePreview();requestDraw();}
    }
  });
  listen(find('#gSearch'),'input',ev=>{
    searchTerm=ev.target.value.trim().toLocaleLowerCase();
    const results=find('#gResults'),matches=nodes.filter(nodeMatches);
    results.hidden=!searchTerm;
    results.innerHTML=matches.length?matches.slice(0,6).map(n=>'<button data-node="'+safe(n.id)+'">'+safe(short(n.label||'未命名记忆',25))+'<span>'+safe(kinds.find(k=>k.id===n.type).name)+'</span></button>').join(''):'<p>这片星图里没有找到</p>';
    find('#gSearchStatus').textContent=searchTerm?'找到 '+matches.length+' 颗记忆 · 搜索范围为当前星图':'显示全部记忆';requestDraw();
  });
  listen(find('#gSearch'),'keydown',ev=>{if(ev.key==='Enter') {const n=nodes.find(nodeMatches);if(searchTerm && n) {selectNode(n,true);clearSearchResults();}}});
  listen(find('#gResults'),'click',ev=>{const b=ev.target.closest('[data-node]');if(b) {selectNode(byId.get(b.dataset.node),true);clearSearchResults();}});
  listen(host,'pointerdown',ev=>{if(!ev.target.closest('.atlas-search')) clearSearchResults();});
  listen(document,'visibilitychange',syncCruise);listen(window,'scroll',syncCruise,{passive:true});
  listen(reducedMotion,'change',()=>{if(reducedMotion.matches) setAutoRotate(false);});
  find('#gGroups').innerHTML=types.map(t=>'<button data-type="'+t.id+'" aria-pressed="false"><i style="background:'+t.color+'"></i><span>'+t.name+'</span><b>'+t.nodes.length+'</b></button>').join('');
  find('#gMap').innerHTML='<div class="atlas-map-orbit"></div>'+types.map((t,i)=>{const angle=-Math.PI/2+i*Math.PI*2/types.length;return '<button data-type="'+t.id+'" aria-pressed="false" aria-label="聚焦'+t.name+'" title="'+t.name+'" style="--star-color:'+t.color+';left:'+(50+Math.cos(angle)*32)+'%;top:'+(50+Math.sin(angle)*30)+'%"></button>';}).join('');
  for(const container of [find('#gGroups'),find('#gMap')]) listen(container,'click',ev=>{const b=ev.target.closest('[data-type]');if(b) focusType(b.dataset.type);});
  const observer=new ResizeObserver(resize);observer.observe(canvas);
  listen(window,'xiaoyu:appearance',updatePalette);
  arrange();updatePalette();resize();
  find('#gSearchStatus').textContent=nodes.length+' 颗记忆 · '+edges.length+' 条真实关联';

  return {
    resetView,relayout,setAutoRotate,
    getState() {return {frame,auto,flat,showLabels,showLinks,selectedId:selected?selected.id:null,activeType,view:{...view},nodeCount:nodes.length,edgeCount:edges.length,clusterCount:clusters.length,destroyed,points:[...points.values()].map(p=>({id:p.node.id,x:p.x,y:p.y,r:p.r,type:p.node.type}))};},
    destroy() {
      destroyed=true;if(pending) cancelAnimationFrame(pending);if(autoFrame) cancelAnimationFrame(autoFrame);
      observer.disconnect();listeners.forEach(remove=>remove());pointers.clear();points.clear();
      host.classList.remove('is-expanded');
    },
  };
}
