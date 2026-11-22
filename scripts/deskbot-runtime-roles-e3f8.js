/* Active expression roles. Authoring drafts and USB commands keep their own owners. */
(function (root) {
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const phaseNames={entry:'进入',loop:'持续',exit:'退出'};
  const semanticHints={neutral:'IDLE',happy:'HAPPY',sad:'SAD',embarrassed:'SHY',confused:'CONFUSED',sleepy:'DROWSY'};
  function create(data,catalog,host){
    const byId=new Map(catalog.roles.map(r=>[r.id,r])),reduced=matchMedia('(prefers-reduced-motion: reduce)');
    let runtime=null,semantic='neutral',result=null,desired=null,working=false,error='',loading=false,resolving=false,previewError='';
    let generation=0,resolveId=0,syncId=0,scheduler=null,raf=0,last=0,elapsed=0,tick=0,roleTime=0,paused=reduced.matches;
    const images=new Map(),imageLoads=new Map();
    const active=()=>byId.get(runtime?.activation?.role_id);
    const identity=()=>JSON.stringify([runtime?.catalog_id,runtime?.activation?.role_id,runtime?.activation?.revision]);
    const visible=()=>!!document.getElementById('runtime-role-slot');
    const ready=()=>runtime?.activation&&runtime.catalog_id===catalog.catalog_id&&!runtime.error&&!error;
    function stop(){cancelAnimationFrame(raf);raf=0;last=0;elapsed=0;}
    function invalidate(){generation++;resolveId++;result=null;scheduler=null;roleTime=0;resolving=false;previewError='';stop();}
    async function request(path,value){
      const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),6000);
      try{
        const response=await fetch(path,{method:value?'POST':'GET',cache:'no-store',signal:controller.signal,
          ...(value?{headers:{'Content-Type':'application/json','X-DeskBot-Studio':'e3f8'},body:JSON.stringify(value)}:{})});
        const body=await response.json();
        if(!response.ok){const failure=Error(body.error||'角色请求暂未完成');failure.state=body.state;throw failure;}
        return body;
      }finally{clearTimeout(timeout);}
    }
    function accept(next,{preservePreview=false}={}){
      if(next.catalog_id!==catalog.catalog_id){invalidate();error='角色目录已更新，请刷新页面。';runtime=null;return false;}
      if(runtime?.activation&&next.activation&&next.activation.revision<runtime.activation.revision)return false;
      const before=identity();runtime=next;
      if(before!==identity()){
        if(preservePreview&&result){generation++;resolveId++;resolving=false;previewError='';}
        else invalidate();
        semantic='neutral';
      }
      header();return true;
    }
    function header(){
      const el=document.getElementById('active-role-label');
      if(el)el.textContent=active()?'电脑角色 · '+active().name:'选择电脑角色';
    }
    function payload(field,value){return {[field]:value,expected_revision:runtime?.activation?.revision??0,catalog_id:catalog.catalog_id};}
    async function sync(){
      if(working)return;
      const id=++syncId;loading=true;paint();
      try{
        const next=await request('/api/roles/state');
        if(id!==syncId||working)return;
        error='';accept(next);
        if(ready()&&visible()&&!result)await resolve();
      }catch(failure){if(id===syncId)error=location.protocol==='file:'?'请启动本机工作台，再保存和切换角色。':failure.name==='AbortError'?'连接角色服务后重试。':failure.message;}
      finally{if(id===syncId){loading=false;paint();}}
    }
    async function choose(id,recover=false){
      if(!byId.has(id))return;
      desired=id;error='';previewError='';resolveId++;resolving=false;paint();
      if(working)return;
      if(!runtime){desired=null;await sync();return;}
      working=true;syncId++;
      try{
        // Saves are serialized. A click back to A while B is saving remains a real pending choice.
        while(desired){
          const target=desired;
          const next=await request(recover?'/api/roles/recover':'/api/roles/activate',payload('role_id',target));
          recover=false;accept(next,{preservePreview:!!result});
          if(desired===target)desired=null;
          paint();
        }
      }catch(failure){
        desired=null;
        if(failure.state)accept(failure.state,{preservePreview:!!result});
        else{
          // A lost reply may follow a successful disk write. Read that state before the next click.
          try{accept(await request('/api/roles/state'),{preservePreview:!!result});}catch(_){invalidate();runtime=null;header();}
        }
        error=failure.name==='AbortError'?'角色保存回复超时，请查看当前选择后重试。':failure.message;
      }finally{
        working=false;loading=false;paint();
        if(ready()&&visible())await resolve();
      }
    }
    async function resolve(){
      if(!ready()||working||!visible())return;
      const id=++resolveId,epoch=generation,key=identity(),wanted=semantic;
      resolving=true;previewError='';paint();
      try{
        const next=await request('/api/roles/expression',payload('semantic',wanted));
        // Load the complete role before starting its clock: phase changes never wait for an atlas download.
        if(next.expression.mode==='composable')await Promise.all(Object.values(byId.get(next.activation.role_id)?.expressions||{}).map(imageFor));
        else await officialImage(next.expression);
        if(id!==resolveId||epoch!==generation||key!==identity()||!visible())return;
        if(next.catalog_id!==catalog.catalog_id||next.activation.role_id!==runtime.activation.role_id||
          next.activation.revision!==runtime.activation.revision||next.expression.semantic!==wanted)return;
        const roleChanged=!!result&&result.activation.role_id!==next.activation.role_id;
        if(roleChanged){scheduler=null;roleTime=0;stop();}
        result=next;
        if(next.expression.mode==='composable'){
          const expressions=active().expressions;
          if(!scheduler){
            const clips=Object.fromEntries(Object.values(expressions).map(e=>[e.state,e.clips]));
            scheduler=new root.GrokRoleScheduler(clips,expressions.neutral.state);scheduler.setPaused(paused);
          }
          scheduler.request(next.expression.state);
        }else{scheduler=null;roleTime=0;}
      }catch(failure){
        if(id!==resolveId||epoch!==generation||!visible())return;
        // Keep the last successful scene with its own role label; a changed catalog clears it in accept().
        if(failure.state)accept(failure.state,{preservePreview:!!result});
        previewError=failure.name==='AbortError'?'表情解析超时，请重试。':failure.message;
        paint();
      }finally{
        if(id===resolveId&&epoch===generation){resolving=false;paint();}
      }
    }
    function select(id){
      if(!catalog.semantics.includes(id)||working||!ready())return;
      semantic=id;previewError='';resolve();
    }
    function imageFor(binding){
      if(images.has(binding.state))return Promise.resolve(images.get(binding.state));
      if(imageLoads.has(binding.state))return imageLoads.get(binding.state);
      const load=new Promise((resolve,reject)=>{
        const image=new Image();image.onload=()=>{images.set(binding.state,image);resolve(image);};
        image.onerror=()=>{imageLoads.delete(binding.state);reject(Error('角色预览加载失败，请重试。'));};
        image.src=binding.atlas;
      });
      imageLoads.set(binding.state,load);return load;
    }
    function grokCanvas(binding,phase,cls=''){
      imageFor(binding).then(()=>{draw();}).catch(failure=>mediaFailure('data-runtime-state',binding.state,failure.message));
      return `<canvas width="360" height="360" class="${cls}" data-runtime-state="${binding.state}" data-runtime-phase="${phase}" aria-label="${esc(binding.label)}${phaseNames[phase]||''}预览"></canvas>`;
    }
    function officialImage(binding){
      const key='official:'+binding.resource_id,asset=data.assets[binding.resource_id];
      if(images.has(key))return Promise.resolve(images.get(key));
      if(imageLoads.has(key))return imageLoads.get(key);
      if(!asset?.atlas)return Promise.resolve(null);
      const load=new Promise((resolve,reject)=>{
        const image=new Image();image.onload=()=>{images.set(key,image);resolve(image);};
        image.onerror=()=>{imageLoads.delete(key);reject(Error('原有角色预览加载失败，请重试。'));};image.src=asset.atlas;
      });imageLoads.set(key,load);return load;
    }
    function officialCanvas(binding,cls='',staticFrame=false){
      officialImage(binding).then(()=>draw()).catch(failure=>mediaFailure('data-runtime-official',binding.semantic,failure.message));
      return `<canvas width="360" height="360" class="${cls}" data-runtime-official="${binding.semantic}" data-runtime-static="${staticFrame}" data-layout="${binding.layout_id}" aria-label="${esc(binding.label)}圆屏预览"></canvas>`;
    }
    function mediaFailure(attribute,value,note){
      // A decorative avatar can fail independently; only resolve() owns active-preview failures and retry.
      document.querySelectorAll(`#runtime-role-slot canvas[${attribute}="${value}"]`).forEach(canvas=>{
        canvas.title=note;canvas.dataset.mediaStatus='unavailable';
        const ctx=canvas.getContext('2d');ctx.fillStyle='#000';ctx.fillRect(0,0,360,360);
        ctx.fillStyle='#b4c2e4';ctx.textAlign='center';ctx.font='28px system-ui';ctx.fillText('预览待加载',180,188);
      });
    }
    function badge(role){return role.resource_stage==='reference_e145'?'E1.45 设备资源基线':'电脑预览 · 尚未安装';}
    function cards(){return `<div class="runtime-choices">${catalog.roles.map(role=>{
      const selected=runtime?.activation?.role_id===role.id,pending=desired===role.id;
      const binding=role.expressions.neutral;
      return `<button type="button" class="runtime-choice" data-role-enable="${role.id}" aria-pressed="${selected}" ${!runtime||runtime.error?'disabled':''}>
        <span class="runtime-avatar">${binding.mode==='single'?officialCanvas(binding,'',true):grokCanvas(binding,'loop')}</span>
        <span class="runtime-choice-copy"><strong>${esc(role.name)}</strong><small>${esc(badge(role))}</small></span>
        <span class="runtime-choice-check">${pending?'保存中':selected?'电脑当前':'选择预览'}</span></button>`;
    }).join('')}</div>`;}
    function graph(){
      const binding=result?.expression,role=byId.get(result?.activation?.role_id)||active();
      if(!binding)return `<div class="runtime-empty">${working?'正在保存角色选择…':loading?'读取角色选择…':'选择表情，查看当前角色的链路。'}</div>`;
      const single=binding.mode==='single',current=scheduler?.snapshot(),show=single?binding:
        Object.values(role.expressions).find(e=>e.state===current?.current)||binding;
      const preview=single?officialCanvas(binding,'runtime-screen'):grokCanvas(show,'current','runtime-screen');
      return `<div class="runtime-stage"><div class="runtime-display"><span class="eyebrow">COMPUTER PREVIEW</span><div class="runtime-display-disc">${preview}</div><div class="runtime-now" id="runtime-now">${esc(role.name)} · ${esc(binding.label)}</div><button type="button" class="button button-small" data-runtime-pause>${paused?'继续播放':'暂停预览'}</button></div>
      <div class="runtime-flow"><div class="runtime-route"><span>${esc(role.name)}</span><i>→</i><span data-runtime-target>${esc(binding.label)}</span><i>→</i><span>${binding.mirror?'双眼镜像':'完整脸 · 无镜像'}</span></div>
      <div class="runtime-node-row ${single?'runtime-single':''}">${single?
        `<div class="runtime-node" data-active="true"><div class="runtime-node-title"><b>表情动画</b><span>${binding.fps} fps</span></div>${officialCanvas(binding)}<code>${esc(binding.resource_id)}</code><small>${binding.loop?'循环播放':'播放一次'}${binding.implicit_action_id?' · 附带动作 '+esc(binding.implicit_action_id):''}</small></div>`:
        ['entry','loop','exit'].map(phase=>`<div class="runtime-node" data-runtime-node="${phase}" data-active="false"><div class="runtime-node-title"><b>${phaseNames[phase]}</b><span data-runtime-duration>${show.clips[phase].duration_ms/1000} s</span></div>${grokCanvas(show,phase)}<code data-runtime-file="${phase}">${esc(show.clips[phase].file.split('/').pop())}</code><div class="runtime-progress"><i></i></div></div>`).join('')}
      </div><p class="runtime-flow-note">${single?'当前映射取自 E1.45 已安装资源快照。':'切换时：当前表情退出 → 共享姿态 → 新表情进入。快速选择只保留最后目标。'}</p>
      <div class="runtime-device-note"><span>角色资源依据</span><strong>${single?'E1.45 留存快照':'圆团候选 · 下一步安装'}</strong><a href="#connection" data-navigate="connection">查看设备实时状态 ↗</a></div></div></div>`;
    }
    function controls(){
      const role=active(),unavailable=runtime?.storage_status==='unavailable';
      return `<div class="runtime-heading"><div><div class="eyebrow">TWO CHARACTERS · ONE BODY</div><h1>选一个角色，看它如何表达。</h1><p>选择自动保存在电脑；重新打开，继续使用上次的角色。</p></div><span class="runtime-saved">${working?'正在保存…':runtime?.storage_status==='saved'?'选择已保存':'电脑角色'}</span></div>
      ${cards()}<div class="runtime-toolbar"><div><span class="eyebrow">当前电脑角色</span><h2>${esc(role?.name||'等待角色选择')}</h2></div><div class="runtime-semantics" aria-label="角色常用表情">${catalog.semantics.map(id=>`<button type="button" data-role-semantic="${id}" aria-label="${esc(catalog.roles[0].expressions[id].label)}" aria-pressed="${semantic===id}" ${!ready()||working?'disabled':''}>${esc(catalog.roles[0].expressions[id].label)}<small aria-hidden="true">${semanticHints[id]}</small></button>`).join('')}</div></div>
      <p id="runtime-pending" class="runtime-pending" role="status">${role&&result&&role.id!==result.activation.role_id?'已保存 '+esc(role.name)+' · 正在切换预览（当前画面：'+esc(byId.get(result.activation.role_id)?.name||'原角色')+'）':resolving?'正在准备 · '+esc(role?.expressions?.[semantic]?.label||semantic):''}</p>
      <p id="runtime-message" class="runtime-message" role="status"></p>${error||previewError?`<button class="button button-small" data-runtime-retry>${previewError?'重试所选表情':'重新读取角色选择'}</button>`:''}${unavailable?'<div class="runtime-recovery"><p>原保存文件将另存为历史记录，再保存所选角色。</p>'+catalog.roles.map(r=>`<button class="button" data-role-recover="${r.id}" ${working?'disabled':''}>重新保存 · ${esc(r.name)}</button>`).join('')+'</div>':''}`;
    }
    function content(){
      return `<div id="runtime-controls">${controls()}</div><div id="runtime-expression">${graph()}</div><div class="runtime-next"><span>下一步</span><strong>双角色上机</strong><span>→</span><span>换壳自动切换</span><span>→</span><span>人格与声音</span></div>`;
    }
    function view(){return '<section id="runtime-role-slot" aria-label="当前角色与表达"></section>';}
    function message(){const el=document.getElementById('runtime-message');if(el){el.textContent=error||previewError||runtime?.error||'';el.dataset.error=String(!!(error||previewError||runtime?.error));}}
    function paint(){
      header();const slot=document.getElementById('runtime-role-slot');if(!slot)return;
      // Keep the actual canvas and phase nodes during same-role changes: neither their clock nor CSS restarts.
      const key=result?JSON.stringify([result.activation.role_id,result.activation.revision,result.expression.mode,
        result.expression.mode==='single'?result.expression.semantic:'composable']):'';
      const previous=document.getElementById('runtime-expression'),keep=!!key&&previous?.dataset.scene===key;
      const focused=slot.contains(document.activeElement)?document.activeElement:null;
      const focusAttr=['data-role-enable','data-role-semantic','data-runtime-pause','data-runtime-retry'].find(a=>focused?.hasAttribute(a));
      if(keep)slot.querySelector('#runtime-controls').innerHTML=controls();
      else{slot.innerHTML=content();document.getElementById('runtime-expression').dataset.scene=key;}
      const role=active(),target=slot.querySelector('[data-runtime-target]');if(target)target.textContent=
        resolving&&semantic!==result.expression.semantic&&role?.id===result.activation.role_id?
          role.expressions[semantic].label:result.expression.label;
      const pause=slot.querySelector('[data-runtime-pause]');if(pause)pause.textContent=paused?'继续播放':'暂停预览';
      const stage=slot.querySelector('.runtime-stage');if(stage)stage.dataset.previewPaused=String(paused);
      if(focusAttr){const value=focused.getAttribute(focusAttr);slot.querySelector(`[${focusAttr}="${value}"]`)?.focus({preventScroll:true});}
      message();host.paint();draw();start();
    }
    function drawFrame(canvas,binding,phase,frame){
      const image=images.get(binding.state);if(!image)return;
      const format=binding.atlas_format,index=binding.clips[phase].atlas_offset+frame,ctx=canvas.getContext('2d');
      ctx.clearRect(0,0,360,360);ctx.fillStyle='#000';ctx.fillRect(0,0,360,360);
      ctx.drawImage(image,index%format.columns*format.size,Math.floor(index/format.columns)*format.size,
        format.size,format.size,0,0,360,360);
      canvas.dataset.frame=String(frame);canvas.dataset.playingState=binding.state;canvas.dataset.playingPhase=phase;
    }
    function draw(){
      if(!visible())return;
      document.querySelectorAll('#runtime-role-slot canvas[data-runtime-official]').forEach(canvas=>{
        const binding=catalog.roles[0].expressions[canvas.dataset.runtimeOfficial],asset=data.assets[binding.resource_id];
        const image=images.get('official:'+binding.resource_id),seconds=canvas.dataset.runtimeStatic==='true'?.55:roleTime/1000;
        const frame=root.DeskBotStudioCore.frameAt({...asset,fps:binding.fps,loop:binding.loop},seconds),ctx=canvas.getContext('2d');
        ctx.clearRect(0,0,canvas.width,canvas.height);ctx.fillStyle='#000';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.save();
        if(canvas.dataset.layout!=='raw'){ctx.beginPath();ctx.arc(180,180,180,0,Math.PI*2);ctx.clip();}
        if(image){const w=asset.frame_width,h=asset.frame_height,x=frame%asset.columns*w,y=Math.floor(frame/asset.columns)*h;
          if(canvas.dataset.layout==='raw')ctx.drawImage(image,x,y,w,h,0,0,w,h);
          else if(canvas.dataset.layout==='eye_anim')DeskBotMirrorRender.draw(ctx,image,x,y,w,h);
          else ctx.drawImage(image,x,y,w,h,Math.floor((360-w)/2),canvas.dataset.layout==='listen_anim'?25:Math.floor((360-h)/2),w,h);
        }else{ctx.fillStyle='#aebdb7';ctx.textAlign='center';ctx.font='14px system-ui';ctx.fillText(asset.atlas?'加载预览…':'请使用本机工作台预览',180,180);}
        ctx.restore();canvas.dataset.frame=String(frame);
      });
      const current=scheduler?.snapshot(),expressions=(byId.get(result?.activation?.role_id)||active())?.expressions;
      const binding=current&&Object.values(expressions||{}).find(e=>e.state===current.current);
      document.querySelectorAll('#runtime-role-slot canvas[data-runtime-state]').forEach(canvas=>{
        let e=Object.values(catalog.roles[1].expressions).find(e=>e.state===canvas.dataset.runtimeState);
        let phase=canvas.dataset.runtimePhase,frame=0;
        if(canvas.closest('#runtime-expression')&&binding){e=binding;phase=phase==='current'?current.phase:phase;
          frame=phase===current.phase?current.frame:paused?0:tick%e.clips[phase].frames;}
        else if(e){phase=phase==='current'?'loop':phase;frame=Math.min(e.clips[phase].frames-1,10);}
        if(e){canvas.setAttribute('aria-label',e.label+(phaseNames[phase]||'')+'预览');drawFrame(canvas,e,phase,frame);}
      });
      if(!current||!binding)return;
      const now=document.getElementById('runtime-now');
      if(now)now.textContent=binding.label+' · '+phaseNames[current.phase]+(current.target!==current.current?' → '+result?.expression.label:'');
      document.querySelectorAll('[data-runtime-node]').forEach(node=>{
        const phase=node.dataset.runtimeNode,on=phase===current.phase;node.dataset.active=String(on);
        node.querySelector('i').style.width=(on?(current.frame+1)/current.frames*100:0)+'%';
        node.querySelector('code').textContent=binding.clips[phase].file.split('/').pop();
        node.querySelector('[data-runtime-duration]').textContent=binding.clips[phase].duration_ms/1000+' s';
      });
    }
    function animate(now){
      raf=0;if(!visible()||document.hidden||!result||paused)return;
      if(last){const delta=Math.min(now-last,250);roleTime+=delta;elapsed+=delta;while(elapsed>=50){scheduler?.advance();tick++;elapsed-=50;}}
      last=now;draw();raf=requestAnimationFrame(animate);
    }
    function start(){if(!raf&&result&&!paused&&visible()&&!document.hidden)raf=requestAnimationFrame(animate);}
    function mount(){
      if(!visible()){invalidate();return;}
      paint();if(ready()&&!working&&!result)resolve();
    }
    function init(){
      document.addEventListener('click',event=>{
        const el=event.target.closest('[data-role-enable],[data-role-recover],[data-role-semantic],[data-runtime-pause],[data-runtime-retry]');
        if(!el||el.disabled)return;
        if(el.hasAttribute('data-role-enable'))choose(el.dataset.roleEnable);
        else if(el.hasAttribute('data-role-recover'))choose(el.dataset.roleRecover,true);
        else if(el.hasAttribute('data-role-semantic'))select(el.dataset.roleSemantic);
        else if(el.hasAttribute('data-runtime-retry')){if(previewError)resolve();else sync();}
        else{paused=!paused;scheduler?.setPaused(paused);stop();paint();}
      });
      window.addEventListener('focus',()=>sync());
      document.addEventListener('visibilitychange',()=>{stop();if(!document.hidden){sync();start();}});
      reduced.addEventListener('change',event=>{paused=event.matches;scheduler?.setPaused(paused);stop();paint();});
      sync();
    }
    return Object.freeze({view,mount,init,state:()=>JSON.parse(JSON.stringify({runtime,semantic,result,desired,working,error,previewError,resolving,paused,
      playback:scheduler?.snapshot()||null}))});
  }
  root.DeskBotRuntimeRoles=Object.freeze({create});
})(globalThis);
