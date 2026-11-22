/* Two-role editing and offline package export. Separate from the active device role. */
(function(root){
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const clone=v=>JSON.parse(JSON.stringify(v)),phases=['entry','loop','exit'];
  const keys=(v,k)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join('|')===[...k].sort().join('|');
  function create(data,catalog,initial){
    let draft=null,history=[],cursor=-1,loading=true,busy=false,error='',report=null,saved=false,painting=false,paintQueued=false;
    const images=new Map(),scaled=new Map(),reduced=matchMedia('(prefers-reduced-motion: reduce)');let raf=0,last=0,elapsed=0,chosen='neutral',scheduler=null,scene='',phaseElapsed=0,paused=reduced.matches;
    const role=()=>draft?.roles.find(r=>r.id===draft.selected_role_id),semantic=()=>chosen;
    const expression=()=>catalog.expressions[role()?.bindings?.[semantic()]?.expression_id];
    const sources=expr=>Object.entries(expr?.resources||{}).map(([phase,id])=>({phase,id,asset:catalog.assets[id]}));
    async function request(path,value){const headers=value?{'Content-Type':'application/json','X-DeskBot-Studio':'e3f9b'}:{},controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
      try{const response=await fetch(path,{method:value?'POST':'GET',headers,cache:'no-store',body:value?JSON.stringify(value):undefined,signal:controller.signal});
        const body=await response.json();if(!response.ok)throw Error(body.error||'本机角色包请求失败');return body;
      }finally{clearTimeout(timeout);}}
    function commit(next){validate(next);if(JSON.stringify(next)===JSON.stringify(draft))return;draft=clone(next);history=history.slice(0,cursor+1);history.push(clone(next));if(history.length>50)history.shift();cursor=history.length-1;report=null;persist();paint();}
    function validate(value){
      if(!keys(value,['schema','catalog_id','selected_role_id','roles'])||value.schema!=='deskbot.role_pack_draft/v1'||value.catalog_id!==catalog.catalog_id||!Array.isArray(value.roles)||value.roles.length!==2)throw Error('草稿目录或结构不匹配');
      const expected=new Map(catalog.roles.map(r=>[r.id,r]));
      const ids=new Set();for(const r of value.roles){if(!keys(r,['id','name','mode','bindings','mirror_overrides'])||!expected.has(r.id)||ids.has(r.id)||r.mode!==expected.get(r.id).mode||!keys(r.bindings,catalog.semantics)||!r.mirror_overrides||typeof r.mirror_overrides!=='object'||Array.isArray(r.mirror_overrides)||typeof r.name!=='string'||!r.name.trim()||[...r.name].length>40||/[\u0000-\u001f]/.test(r.name))throw Error('角色草稿格式无效');ids.add(r.id);
        for(const [id,v] of Object.entries(r.mirror_overrides))if(!Object.hasOwn(catalog.assets,id)||catalog.assets[id].mode!==r.mode||typeof v!=='boolean')throw Error('镜像选项无效');
        const home=new Set();for(const sem of catalog.semantics){const b=r.bindings[sem],item=catalog.expressions[b?.expression_id];if(!keys(b,['expression_id','fps','loop'])||!item||item.mode!==r.mode||!Number.isInteger(b.fps)||b.fps<1||b.fps>60||typeof b.loop!=='boolean'||r.mode==='composable'&&!b.loop)throw Error('表情连接或播放设置无效');
          if(r.mode==='composable'){const settings=sources(item).map(({id,asset})=>r.mirror_overrides[id]??asset.mirror);if(new Set(settings).size!==1)throw Error('完整角色三段动画请使用统一镜像设置');home.add(settings[0]);}}
        if(home.size>1)throw Error('完整角色各表情请使用统一镜像设置');
      }
      if(!value.roles.some(r=>r.id===value.selected_role_id))throw Error('当前角色不存在');
    }
    function persist(){try{localStorage.setItem('deskbot.role-package.e3f9b',JSON.stringify(draft));saved=true;}catch(_){saved=false;}}
    function changed(action){const next=clone(draft),r=next.roles.find(x=>x.id===next.selected_role_id),sem=semantic(),b=r.bindings[sem],item=catalog.expressions[b.expression_id];action(r,b,item,next);commit(next);}
    function imageFor(asset){const url=asset.mode==='single'?data.assets[asset.source_asset_id]?.atlas:asset.atlas;if(images.has(url))return images.get(url);const img=new Image();img.onload=()=>{document.querySelectorAll('[data-package-asset]').forEach(c=>delete c.dataset.paintKey);if(paused)drawAll();};img.onerror=()=>{error='动画预览加载失败，请刷新工作台。';const el=document.querySelector('[data-pack-status]');if(el)el.textContent=error;};img.src=url;images.set(url,img);return img;}
    function draw(canvas,asset,frame,mirror){const img=imageFor(asset),key=[asset.id,frame,mirror,img.complete&&img.naturalWidth].join(':');if(canvas.dataset.paintKey===key)return;canvas.dataset.paintKey=key;const ctx=canvas.getContext('2d');ctx.clearRect(0,0,360,360);ctx.fillStyle='#000';ctx.fillRect(0,0,360,360);if(!img.complete||!img.naturalWidth)return;
      const fmt=asset.atlas_format,w=fmt.width,h=fmt.height,index=asset.atlas_offset+frame,sx=index%fmt.columns*w,sy=Math.floor(index/fmt.columns)*h;
      ctx.save();ctx.beginPath();ctx.arc(180,180,180,0,Math.PI*2);ctx.clip();
      if(mirror&&root.DeskBotMirrorRender){let source=img,x=sx,y=sy;if(w!==asset.width||h!==asset.height){const cacheKey=asset.id+':'+frame;let enlarged=scaled.get(cacheKey);if(!enlarged){enlarged=document.createElement('canvas');enlarged.width=asset.width;enlarged.height=asset.height;enlarged.getContext('2d').drawImage(img,sx,sy,w,h,0,0,asset.width,asset.height);scaled.set(cacheKey,enlarged);if(scaled.size>80)scaled.delete(scaled.keys().next().value);}source=enlarged;x=y=0;}root.DeskBotMirrorRender.draw(ctx,source,x,y,asset.width,asset.height);}
      else ctx.drawImage(img,sx,sy,w,h,Math.floor((360-asset.width)/2),Math.floor((360-asset.height)/2),asset.width,asset.height);
      ctx.restore();canvas.dataset.frame=String(frame);canvas.dataset.renderedAsset=asset.id;canvas.dataset.renderedMirror=String(mirror);
    }
    function canvas(asset,mirror,cls){return `<canvas width="360" height="360" class="role-package-canvas ${cls||''}" data-package-asset="${esc(asset.id)}" data-package-mirror="${mirror}"></canvas>`;}
    function currentRoleView(){const r=role(),sem=semantic(),binding=r.bindings[sem],expr=catalog.expressions[binding.expression_id],nodes=sources(expr),original=initial.roles.find(x=>x.id===r.id).bindings[sem],was=catalog.expressions[original.expression_id],replaced=original.expression_id!==binding.expression_id;
      return `<section class="role-package-editor" id="role-package-editor"><header class="role-package-heading"><div><span class="eyebrow">ROLE AUTHORING · OFFLINE</span><h2>角色编辑与资源包</h2><p>完整新角色包含 29 种表情。设备安装所选角色，另一套保留在电脑，连接后可换回。</p></div><div class="role-package-actions"><button type="button" data-pack-undo ${cursor<=0?'disabled':''} aria-label="撤销">↶</button><button type="button" data-pack-redo ${cursor>=history.length-1?'disabled':''} aria-label="重做">↷</button><button type="button" data-pack-export>导出草稿</button><button type="button" data-pack-import>导入草稿</button><input type="file" data-pack-file accept="application/json,.json" hidden></div></header>
      <div class="role-package-character-row">${catalog.roles.map(x=>`<button type="button" data-pack-role="${esc(x.id)}" aria-pressed="${r.id===x.id}"><strong>${esc(x.name)}</strong><small>${x.mode==='single'?'单段动画':'进入 · 持续 · 退出'}</small></button>`).join('')}</div>
      <div class="role-package-semantics">${catalog.semantics.map(s=>`<button type="button" data-pack-semantic="${s}" aria-pressed="${sem===s}">${esc(catalog.labels[s])}</button>`).join('')}</div>
      <div class="role-package-graph"><div class="role-package-preview"><span class="eyebrow">CIRCLE PREVIEW</span>${canvas(nodes[0].asset,r.mirror_overrides[nodes[0].id]??nodes[0].asset.mirror,'role-package-main-preview')}<strong>${esc(r.name)} · ${esc(catalog.labels[sem])}</strong><small>${binding.fps} fps · ${binding.loop?'循环':'播放一次'}</small><span class="role-package-phase" data-pack-phase></span><button type="button" data-pack-pause>${paused?'播放预览':'暂停预览'}</button><label>帧率 <input type="number" min="1" max="60" step="1" data-pack-fps value="${binding.fps}"></label><label class="role-package-loop"><input type="checkbox" data-pack-loop ${binding.loop?'checked':''} ${r.mode==='composable'?'disabled':''}>${r.mode==='composable'?'持续循环 · 切换时退出':'循环播放'}</label></div>
      <div class="role-package-nodes">${nodes.map(({phase,id,asset})=>{const mirror=r.mirror_overrides[id]??asset.mirror;return `<article class="role-package-node" data-pack-node="${phase}"><div><small>${esc(phase==='single'?'动画资源':phase==='entry'?'进入':phase==='loop'?'持续':'退出')}</small><strong>${esc(asset.name)}</strong><span>${asset.frames} 帧 · ${asset.bytes.toLocaleString()} B</span></div>${canvas(asset,mirror,'')}<label><input type="checkbox" data-pack-mirror="${esc(id)}" ${mirror?'checked':''}> 镜像</label><small>${Object.hasOwn(r.mirror_overrides,id)?'手动设置':'素材默认'}</small><button type="button" class="mirror-auto" data-pack-auto="${esc(id)}" ${Object.hasOwn(r.mirror_overrides,id)?'':'disabled'}>恢复自动</button></article>`;}).join('')}</div></div>${r.mode==='composable'?'<p class="role-package-seam-note">为保持切换接点连续，调整镜像会同步到完整角色全部三段动画。</p>':''}
      <div class="role-package-replace"><label>替换整段动画<select data-pack-expression>${Object.values(catalog.expressions).filter(x=>x.mode===r.mode).map(x=>`<option value="${esc(x.id)}" ${x.id===binding.expression_id?'selected':''}>${esc(x.label)}</option>`).join('')}</select></label><small>${r.mode==='composable'?'替换完整三段组合，保留无缝接点':'连接到已登记的单段动画'}</small></div>${replaced?`<div class="role-package-comparison"><span class="pack-disconnected">${esc(was.label)} · 原连接已断开</span><span>→</span><strong>${esc(expr.label)} · 新连接生效</strong><button type="button" data-pack-restore>恢复原连接</button></div>`:''}
      <p data-pack-status class="${error?'role-package-error':''}" role="status">${esc(error)}</p>${report?reportView(report):''}<div class="role-package-footer"><span>${saved?'角色草稿已自动保存':'草稿在本页 · 可导出保存'} · 离线生成包</span><button type="button" class="package-generate" data-pack-generate ${busy?'disabled':''}>${busy?'正在核对…':'生成并核对所选角色包'}</button></div></section>`;}
    function reportView(r){return `<section class="role-package-result"><div><strong>离线资源包已核对</strong><span>${(r.package_bytes/1048576).toFixed(2)} / ${((r.partition_bytes||r.soft_limit_bytes)/1048576).toFixed(2)} MiB</span></div><p>原包 ${(r.base_bytes/1048576).toFixed(2)} MiB · 新动画 ${(r.added_eaf_bytes/1024).toFixed(1)} KiB · 配置 ${(r.config_bytes/1024).toFixed(1)} KiB · 剩余 ${((r.partition_free_bytes??r.soft_free_bytes)/1024).toFixed(1)} KiB</p><div>${Object.entries(r.downloads).map(([name,url])=>`<a download href="${esc(url)}">${esc(name)}</a>`).join('')}</div><small>包含设备角色图、镜像布局与 8 MiB 资源镜像；设备仍运行当前已安装版本。</small></section>`;}
    function publishView(){return report?reportView(report):'<section class="role-package-editor"><h2>角色包</h2><p>编辑两个角色的动画、镜像和播放方式，然后生成可核对的离线资源包。</p><a href="#roles">打开角色编辑 →</a></section>';}
    function paint(){if(painting){if(!paintQueued){paintQueued=true;queueMicrotask(()=>{paintQueued=false;paint();});}return;}painting=true;try{paintContent();}finally{painting=false;}}
    function paintContent(){const status=document.getElementById('save-state');if(status&&['#roles','#publish'].includes(location.hash))status.textContent=saved?'角色草稿已保存':'角色草稿在本页';const slot=document.getElementById('role-package-slot');if(!slot)return;if(!draft){slot.textContent=loading?'正在载入角色素材…':error;return;}prepareScene();slot.innerHTML=currentRoleView();
      slot.querySelectorAll('[data-pack-role]').forEach(b=>b.onclick=()=>{const next=clone(draft);next.selected_role_id=b.dataset.packRole;commit(next);});
      slot.querySelectorAll('[data-pack-semantic]').forEach(b=>b.onclick=()=>{chosen=b.dataset.packSemantic;if(scheduler)scheduler.request(chosen);else elapsed=0;paint();});
      slot.querySelector('[data-pack-expression]').onchange=e=>changed((r,b)=>{b.expression_id=e.target.value;});
      const restore=slot.querySelector('[data-pack-restore]');if(restore)restore.onclick=()=>changed((r,b)=>Object.assign(b,initial.roles.find(x=>x.id===r.id).bindings[chosen]));
      slot.querySelector('[data-pack-fps]').onchange=e=>{const n=Number(e.target.value);if(Number.isInteger(n)&&n>=1&&n<=60)changed((r,b)=>{b.fps=n;});else paint();};
      slot.querySelector('[data-pack-loop]').onchange=e=>changed((r,b)=>{b.loop=e.target.checked;});
      slot.querySelector('[data-pack-pause]').onclick=()=>{paused=!paused;last=0;paint();};
      slot.querySelectorAll('[data-pack-mirror]').forEach(input=>input.onchange=()=>setMirror(input.dataset.packMirror,input.checked));
      slot.querySelectorAll('[data-pack-auto]').forEach(b=>b.onclick=()=>setMirror(b.dataset.packAuto,null));
      slot.querySelector('[data-pack-undo]').onclick=()=>move(-1);slot.querySelector('[data-pack-redo]').onclick=()=>move(1);
      slot.querySelector('[data-pack-export]').onclick=()=>download(JSON.stringify(draft,null,2),'deskbot-role-draft.json','application/json');
      const file=slot.querySelector('[data-pack-file]');slot.querySelector('[data-pack-import]').onclick=()=>file.click();file.onchange=()=>importFile(file.files[0]);
      slot.querySelector('[data-pack-generate]').onclick=generate;drawAll();if(!raf){last=0;raf=requestAnimationFrame(tick);}
    }
    async function importFile(file){try{if(!file)return;if(file.size>32768)throw Error('草稿应小于 32 KiB');const incoming=JSON.parse(await file.text());validate(incoming);commit(incoming);error='';paint();}catch(e){error=e.message;paint();}}
    function toolbar(action){if(!draft)return;if(action==='export')download(JSON.stringify(draft,null,2),'deskbot-role-draft.json','application/json');
      if(action==='import'){let file=document.querySelector('[data-pack-global-file]');if(!file){file=document.createElement('input');file.type='file';file.accept='.json,application/json';file.hidden=true;file.dataset.packGlobalFile='';file.onchange=()=>importFile(file.files[0]);document.body.append(file);}file.value='';file.click();}}
    function setMirror(id,value){changed(r=>{const ids=r.mode==='composable'?Object.values(catalog.assets).filter(a=>a.mode==='composable').map(a=>a.id):[id];for(const key of ids){if(value===null)delete r.mirror_overrides[key];else r.mirror_overrides[key]=value;}});}
    function move(delta){const next=cursor+delta;if(next<0||next>=history.length)return;cursor=next;draft=clone(history[cursor]);report=null;persist();paint();}
    function download(text,name,type){const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([text],{type}));a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
    async function generate(){if(busy)return;busy=true;error='';const sent=JSON.stringify(draft);paint();try{const next=await request('/api/role-package/generate',{draft:JSON.parse(sent)});if(sent===JSON.stringify(draft))report=next;else error='草稿已调整，请生成当前版本。';}catch(e){error=e.message;}finally{busy=false;paint();}}
    function prepareScene(){const next=JSON.stringify(role());if(scene===next){if(scheduler)scheduler.request(chosen);return;}scene=next;elapsed=phaseElapsed=0;last=0;scheduler=null;if(role().mode==='composable'){const clips=Object.fromEntries(catalog.semantics.map(s=>[s,Object.fromEntries(sources(catalog.expressions[role().bindings[s].expression_id]).map(x=>[x.phase,{frames:x.asset.frames}]))]));scheduler=new root.GrokRoleScheduler(clips,chosen);}}
    function drawAll(){if(!draft)return;const r=role(),binding=r.bindings[chosen],expr=expression();
      document.querySelectorAll('#role-package-slot .role-package-node [data-package-asset]').forEach(c=>{const a=catalog.assets[c.dataset.packageAsset],tick=Math.floor(elapsed*binding.fps),frame=binding.loop?Math.min(a.frames-1,tick%(a.frames+(a.tail_hold_ticks||0))):Math.min(a.frames-1,tick);draw(c,a,frame,c.dataset.packageMirror==='true');});
      const c=document.querySelector('.role-package-main-preview');if(!c)return;let id,frame,phase='single',current=chosen;
      if(scheduler){const snap=scheduler.snapshot();phase=snap.phase;frame=snap.frame;current=snap.current;id=catalog.expressions[r.bindings[current].expression_id].resources[phase];}
      else{id=expr.resources.single;const a=catalog.assets[id],tick=Math.floor(elapsed*binding.fps);frame=binding.loop?Math.min(a.frames-1,tick%(a.frames+(a.tail_hold_ticks||0))):Math.min(a.frames-1,tick);}
      const a=catalog.assets[id];draw(c,a,frame,r.mirror_overrides[id]??a.mirror);const el=document.querySelector('[data-pack-phase]');if(el)el.textContent=scheduler?catalog.labels[current]+' · '+({entry:'进入',loop:'持续',exit:'退出'}[phase]):'单段播放';
      document.querySelectorAll('[data-pack-node]').forEach(n=>n.dataset.playing=String(n.dataset.packNode===phase&&current===chosen));}
    function tick(now){raf=0;if(!document.getElementById('role-package-slot')){last=0;return;}const dt=last?Math.min((now-last)/1000,.2):0;last=now;if(!paused&&!document.hidden){elapsed+=dt;if(scheduler){phaseElapsed+=dt;let count=0;while(count++<24){const fps=role().bindings[scheduler.snapshot().current].fps;if(phaseElapsed<1/fps)break;phaseElapsed-=1/fps;scheduler.advance();}}drawAll();}raf=requestAnimationFrame(tick);}
    async function init(){try{const remote=await request('/api/role-package/catalog');if(remote.catalog.catalog_id!==catalog.catalog_id)throw Error('角色素材目录已更新，请重新载入工作台。');catalog.assets=remote.catalog.assets;catalog.expressions=remote.catalog.expressions;catalog.roles=remote.catalog.roles;catalog.semantics=remote.catalog.semantics;catalog.labels=remote.catalog.labels;catalog.schema=remote.catalog.schema;
        let selected=remote.draft||initial;try{const stored=JSON.parse(localStorage.getItem('deskbot.role-package.e3f9b'));validate(stored);selected=stored;}catch(_){}validate(selected);draft=clone(selected);history=[clone(draft)];cursor=0;loading=false;error='';persist();
      }catch(e){loading=false;error=e.message;}
      paint();}
    function view(){return '<div id="role-package-slot" aria-live="polite"></div>';}
    function mount(){paint();if(!raf){last=0;raf=requestAnimationFrame(tick);}}
    function state(){return {draft:clone(draft),busy,report,error,cursor,history:history.length,semantic:chosen,scheduler:scheduler?.snapshot(),paused,saved};}
    return {view,publishView,init,mount,paint,state,toolbar,move};
  }
  root.DeskBotRolePackageE3f9b={create};
})(globalThis);
