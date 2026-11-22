/* Full character drafts. This module never calls device or expression-activation endpoints. */
(function(root){
  'use strict';
  const copy=v=>JSON.parse(JSON.stringify(v));
  const ordered=v=>Array.isArray(v)?v.map(ordered):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,ordered(v[k])])):v;
  const contents=p=>{const {profile_revision,...fields}=p;return JSON.stringify(ordered(fields));};
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function create(data,catalog){
    let state=null,draft=null,dirty=false,busy=false,message='',failed=false,conflict=null,loadError=false,pendingSave=null;
    let semantic='neutral',scheduler=null,raf=0,last=0,elapsed=0,seconds=0,previewError='',epoch=0;
    const images=new Map(),loads=new Map(),reduced=matchMedia('(prefers-reduced-motion: reduce)');
    let paused=reduced.matches;
    const visible=()=>!!document.getElementById('characters-root');
    const selected=()=>catalog.roles.find(r=>r.id===draft?.expression_role_id);
    const supported=()=>!!state&&catalog.catalog_id===state.expression_catalog.catalog_id;
    const currentReference=()=>supported()&&draft?.expression_graph_sha256===state.expression_catalog.graph_sha256&&
      draft?.preview_catalog_id===state.expression_catalog.catalog_id&&state.expression_catalog.roles.some(r=>r.id===draft?.expression_role_id);
    const newId=()=>'character-'+crypto.randomUUID().replaceAll('-','');
    const roleName=id=>state?.expression_catalog.roles.find(r=>r.id===id)?.name||id;
    async function request(path,value){
      const control=new AbortController(),timer=setTimeout(()=>control.abort(),7000);
      try{
        const r=await fetch('/api/characters/'+path,{method:value?'POST':'GET',cache:'no-store',signal:control.signal,
          ...(value?{headers:{'Content-Type':'application/json','X-DeskBot-Studio':'characters-v1'},body:JSON.stringify(value)}:{})});
        const body=await r.json();if(!r.ok){const e=Error(body.error||'角色请求未完成');e.state=body.state;e.status=r.status;throw e;}return body;
      }finally{clearTimeout(timer);}
    }
    function reference(id){
      draft.expression_role_id=id;draft.expression_graph_sha256=state.expression_catalog.graph_sha256;
      draft.preview_catalog_id=state.expression_catalog.catalog_id;
    }
    function fresh(expressionId){
      return {schema:'deskbot.role_profile.v2',role_id:newId(),display_name:roleName(expressionId),persona:'',
        voice:{provider:'unconfigured',voice_id:'',speaking_style:''},expression_role_id:expressionId,
        expression_graph_sha256:state.expression_catalog.graph_sha256,preview_catalog_id:state.expression_catalog.catalog_id,profile_revision:0};
    }
    function unsaved(){return dirty&&!confirm('草稿还未保存。放弃这次修改并打开另一个角色？');}
    function choose(id){
      if(busy||pendingSave||unsaved())return;const p=state.profiles.find(p=>p.role_id===id);if(!p)return;
      draft=copy(p);dirty=false;conflict=null;message='';previewError='';semantic='neutral';resetPreview();paint();
    }
    function add(id){
      if(busy||pendingSave||!supported()||unsaved())return;
      draft=fresh(id);dirty=true;conflict=null;message='';semantic='neutral';resetPreview();paint();
    }
    function resetPreview(){epoch++;scheduler=null;seconds=0;elapsed=0;last=0;previewError='';stop();}
    async function sync(){
      if(busy)return;busy=true;status();
      try{state=await request('state');loadError=false;failed=false;message='';
        const latest=state.profiles.find(p=>p.role_id===draft?.role_id);
        if(pendingSave){
          const committed=state.profiles.find(p=>p.role_id===pendingSave.role_id);
          if(committed&&committed.profile_revision>pendingSave.profile_revision&&contents(committed)===contents(pendingSave)){
            draft=copy(committed);dirty=false;conflict=null;message='已核对上次保存结果 · 修订 '+draft.profile_revision;resetPreview();
          }else if(committed&&committed.profile_revision>pendingSave.profile_revision){
            conflict=committed;message='该角色已有新修改，当前草稿保留，可载入新版或另存角色';
          }else message='上次修改尚未保存，草稿保留，可重试';
          pendingSave=null;
        }else if(latest){
          if(!dirty){draft=copy(latest);conflict=null;message='已载入最新保存结果 · 修订 '+draft.profile_revision;resetPreview();}
          else if(latest.profile_revision!==draft.profile_revision){conflict=latest;message='另一页面已保存新版，当前草稿保留';}
          else{conflict=null;message='已读取保存结果，当前草稿保留';}
        }else if(!draft&&state.profiles.length){draft=copy(state.profiles[0]);resetPreview();}
      }catch(e){loadError=true;failed=true;message=e.name==='AbortError'?'读取角色超时，请重试':e.message;}
      finally{busy=false;paint();}
    }
    async function save(asNew=false){
      if(!draft||busy||pendingSave)return;
      const outgoing=copy(draft);
      if(asNew){outgoing.role_id=newId();outgoing.profile_revision=0;}
      busy=true;failed=false;pendingSave=outgoing;message='正在保存…';status();
      try{
        const result=await request('save',{profile:outgoing,expected_revision:outgoing.profile_revision});
        state=result.state;draft=copy(result.profile);dirty=false;conflict=null;pendingSave=null;message='已保存在电脑 · 修订 '+draft.profile_revision;
      }catch(e){
        if(e.state){state=e.state;conflict=e.state.profiles.find(p=>p.role_id===draft.role_id)||null;}
        // A timeout can follow a successful disk write. Keep the draft and let the next read expose that revision.
        if(e.status)pendingSave=null;
        failed=true;message=pendingSave?'保存结果待确认，草稿已保留。请重新读取保存结果。':e.message;
      }finally{busy=false;paint();}
    }
    async function migrate(file){
      if(!file||busy||pendingSave||!draft||!supported()||unsaved())return;
      busy=true;failed=false;status();
      try{
        if(file.size>65536)throw Error('旧档案须小于 64 KiB');
        const legacy=JSON.parse(await file.text());
        const result=await request('migrate',{legacy_profile:legacy,expression_role_id:draft.expression_role_id});
        state=result.state;draft=copy(result.profile);dirty=false;conflict=null;message='旧档案已载入为独立角色，外壳关系待确认';resetPreview();
      }catch(e){failed=true;message=e.message;}
      finally{busy=false;paint();}
    }
    function exportDraft(){
      const blob=new Blob([JSON.stringify(draft,null,2)+'\n'],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');
      a.href=url;a.download=draft.role_id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }
    function list(){
      return `<aside class="characters-list"><div class="characters-list-title">我的角色 <span>${state?.profiles.length||0}</span></div>${(state?.profiles||[]).map(p=>
        `<button type="button" data-character-open="${esc(p.role_id)}" aria-pressed="${draft?.role_id===p.role_id}" ${busy?'disabled':''}><span class="characters-dot"></span><span><strong>${esc(p.display_name)}</strong><small>${esc(roleName(p.expression_role_id))} · 修订 ${p.profile_revision}</small></span></button>`).join('')}
        <div class="characters-create"><small>创建新角色</small>${(state?.expression_catalog.roles||[]).map(r=>`<button type="button" data-character-add="${esc(r.id)}" ${busy||!supported()?'disabled':''}>＋ 用${esc(r.name)}创建</button>`).join('')}</div>
      </aside>`;
    }
    function editor(){
      if(!draft)return `<div class="characters-empty"><span class="characters-orbit">✦</span><h2>先认识你的角色</h2><p>选择一种表情形象，再写下它的名字和性格。</p><p>登记外壳后，这份角色会与外壳一起启用。</p></div>`;
      const voice=draft.voice;
      return `<div class="characters-editor"><div class="characters-editor-title"><div><span class="characters-kicker">正在编辑 · 完整角色</span><h2>${esc(draft.display_name||'新角色')}</h2></div><span id="character-saved">${dirty?'草稿未保存':'电脑已保存'}</span></div>
        <form id="character-form"><label>角色名字<input name="display_name" maxlength="80" required value="${esc(draft.display_name)}" placeholder="它叫什么？"></label>
        <label>人格<textarea name="persona" maxlength="2000" rows="4" placeholder="例如：好奇、慢热，喜欢用简单比喻解释事情。">${esc(draft.persona)}</textarea><small>描述它的性格、回应方式与喜好。</small></label>
        <div class="characters-fields"><label>表情形象<select name="expression_role_id">${!state.expression_catalog.roles.some(r=>r.id===draft.expression_role_id)?`<option value="${esc(draft.expression_role_id)}">${esc(draft.expression_role_id)} · 待关联</option>`:''}${state.expression_catalog.roles.map(r=>`<option value="${esc(r.id)}" ${r.id===draft.expression_role_id?'selected':''}>${esc(r.name)}</option>`).join('')}</select></label>
        <label>音色服务<select name="provider">${[['unconfigured','稍后选择'],['xiaozhi','小智服务'],['gateway','角色网关']].map(([v,l])=>`<option value="${v}" ${v===voice.provider?'selected':''}>${l}</option>`).join('')}</select></label></div>
        <label>音色标识<input name="voice_id" maxlength="96" value="${esc(voice.voice_id)}" placeholder="接入音色服务后填写"></label>
        <label>说话风格<textarea name="speaking_style" maxlength="500" rows="2" placeholder="例如：轻快、语速适中，回答简短。">${esc(voice.speaking_style)}</textarea></label>
        <div class="characters-save"><button class="characters-primary" type="submit" ${busy||!currentReference()?'disabled':''}>保存角色</button><button type="button" data-character-action="copy" ${busy||!currentReference()?'disabled':''}>另存角色</button><button type="button" data-character-action="export">导出草稿</button></div>
        </form>
        <details class="characters-import"><summary>导入旧版角色档案</summary><p>将旧档案转成独立角色，表情使用上方所选形象；原外壳关系留作来源记录。</p><p>相同来源与表情版本再次导入，会打开已有角色并保留后续编辑。</p><label>选择 v1 JSON 文件<input type="file" accept=".json,application/json" data-character-import ${busy?'disabled':''}></label>${draft.legacy?`<p>已保留来源：${esc(draft.legacy.profile.display_name)} · 原修订 ${draft.legacy.profile.profile_revision}</p>`:''}</details>
      </div>`;
    }
    function progress(){
      if(!draft)return '';
      return `<div class="characters-progress" aria-label="角色配置进度"><span data-character-profile-progress data-complete="${!dirty&&draft.profile_revision>0}"><b>01</b> 角色档案<small>${dirty?'编辑中':'已保存'}</small></span><i>→</i><span data-character-expression-progress data-complete="${currentReference()}"><b>02</b> 表情形象<small>${currentReference()?'已关联':'待关联'}</small></span><i>→</i><span><b>03</b> 外壳关联<small>下一阶段</small></span><i>→</i><span><b>04</b> 人格与声音<small>配置先存，接入随后</small></span></div>`;
    }
    function previewView(){
      if(!draft)return '';
      const role=selected(),binding=role?.expressions[semantic];
      return `<aside class="characters-preview"><div class="characters-kicker">电脑预览</div><div class="characters-screen"><canvas id="character-canvas" width="360" height="360" aria-label="角色表情预览"></canvas></div>
        <div class="characters-preview-label"><strong>${esc(role?.name||'待选择表情')}</strong><span id="character-phase"></span></div>
        <div class="characters-semantics">${catalog.semantics.map(s=>`<button type="button" data-character-semantic="${s}" aria-pressed="${s===semantic}" ${!currentReference()?'disabled':''}>${esc(role?.expressions[s]?.label||s)}</button>`).join('')}</div>
        <div class="characters-play"><button type="button" data-character-action="pause">${paused?'播放预览':'暂停预览'}</button><span id="character-layout">${binding?.mirror?'双眼镜像':'完整脸 · 无镜像'}</span></div>
        <p id="character-preview-error" role="status"></p><button type="button" data-character-action="preview-retry" hidden>重试预览</button>
        <div class="characters-reference">${currentReference()?`<span>表达资源版本</span><code>${esc(draft.expression_graph_sha256.slice(0,12))}</code>`:`<p>表情版本已变化，请重新关联当前版本。</p><button type="button" data-character-action="relink" ${!supported()?'disabled':''}>重新关联当前表情</button>`}</div>
        <p class="characters-service-note">人格与音色已纳入角色配置，实际语音与试听在服务接入后启用。</p>
        <a href="#roles" data-navigate="roles">打开表情制作与设备角色 ↗</a>
      </aside>`;
    }
    function view(){return '<section id="characters-root" aria-label="我的角色"></section>';}
    function paint(){
      const el=document.getElementById('characters-root');if(!el)return;
      el.innerHTML=`<header class="characters-heading"><div><span class="characters-kicker">YOUR CHARACTERS</span><h1>一个形象，一种性格。</h1><p>创建角色，预览表达；换上外壳时，让它成为熟悉的伙伴。</p></div><a href="#shells" data-navigate="shells">我的外壳 ↗</a></header>
        ${progress()}<div class="characters-feedback" role="status"><p id="character-message"></p><button type="button" data-character-action="refresh" ${busy?'disabled':''}>重新读取保存结果</button><button type="button" data-character-action="load-conflict" ${!conflict||busy?'hidden':''}>载入新版</button></div>
        ${loadError&&!state?'<p>角色库暂未读入，点击重新读取继续。</p>':!supported()&&state?'<p>预览目录已更新，请刷新页面再编辑。</p>':''}
        <div class="characters-workspace">${list()}${editor()}${previewView()}</div>`;
      status();preparePreview();
    }
    function status(){
      const el=document.getElementById('characters-root');if(!el)return;
      const msg=el.querySelector('#character-message');if(msg){msg.textContent=message;msg.dataset.error=String(failed);}
      const locked=busy||!!pendingSave;
      const saved=el.querySelector('#character-saved');if(saved)saved.textContent=busy?'正在处理…':pendingSave?'保存结果待确认':dirty?'草稿未保存':'电脑已保存';
      const progress=el.querySelector('[data-character-profile-progress]');
      if(progress){progress.dataset.complete=String(!dirty&&!pendingSave&&draft.profile_revision>0);progress.querySelector('small').textContent=pendingSave?'待确认':dirty?'编辑中':'已保存';}
      const expr=el.querySelector('[data-character-expression-progress]');if(expr){expr.dataset.complete=String(currentReference());expr.querySelector('small').textContent=currentReference()?'已关联':'待关联';}
      el.querySelectorAll('#character-form input,#character-form textarea,#character-form select').forEach(e=>{e.disabled=locked;});
      el.querySelectorAll('#character-form button').forEach(e=>{e.disabled=e.dataset.characterAction==='export'?busy:locked||!currentReference();});
      el.querySelectorAll('[data-character-open],[data-character-add],[data-character-import]').forEach(e=>{e.disabled=locked||!supported();});
      const refresh=el.querySelector('[data-character-action="refresh"]');if(refresh)refresh.disabled=busy;
      const load=el.querySelector('[data-character-action="load-conflict"]');if(load)load.disabled=locked;
      const relink=el.querySelector('[data-character-action="relink"]');if(relink)relink.disabled=locked||!supported();
    }
    function stop(){cancelAnimationFrame(raf);raf=0;last=0;}
    function loadImage(url){
      if(images.has(url))return Promise.resolve(images.get(url));
      if(!loads.has(url))loads.set(url,new Promise((resolve,reject)=>{
        const image=new Image();image.onload=()=>{images.set(url,image);resolve(image);};
        image.onerror=()=>{loads.delete(url);reject(Error('预览素材暂未载入，请重试'));};image.src=url;
      }));return loads.get(url);
    }
    function makeScheduler(role){
      const bindings=Object.values(role.expressions),initial=role.expressions[semantic];
      scheduler=new GrokRoleScheduler(Object.fromEntries(bindings.map(b=>[b.state,b.clips])),initial.state);
      // Reduced motion still shows the selected expression: sample its loop rather than the shared entry pose.
      if(paused)for(let i=0;i<initial.clips.entry.frames+Math.floor(initial.clips.loop.frames/2);i++)scheduler.advance();
      scheduler.setPaused(paused);
    }
    async function preparePreview(){
      const role=selected(),generation=epoch;
      if(!visible()||!role||!currentReference()){stop();return;}
      try{
        const bindings=Object.values(role.expressions);
        await Promise.all(bindings.map(b=>loadImage(b.mode==='composable'?b.atlas:data.assets[b.resource_id].atlas)));
        if(generation!==epoch||!visible())return;
        if(role.expressions[semantic].mode==='composable'&&!scheduler){
          makeScheduler(role);
        }
        draw();start();
      }catch(e){if(generation===epoch){previewError=e.message;previewMessage();}}
    }
    function previewMessage(){const e=document.getElementById('character-preview-error');if(e)e.textContent=previewError;
      const retry=document.querySelector('[data-character-action="preview-retry"]');if(retry)retry.hidden=!previewError;}
    function draw(){
      const canvas=document.getElementById('character-canvas'),role=selected();if(!canvas||!role||!currentReference())return;
      const ctx=canvas.getContext('2d');ctx.clearRect(0,0,360,360);ctx.fillStyle='#000';ctx.fillRect(0,0,360,360);ctx.save();ctx.beginPath();ctx.arc(180,180,180,0,Math.PI*2);ctx.clip();
      let binding=role.expressions[semantic],frame=0,phase='loop';
      if(binding.mode==='composable'){
        const s=scheduler?.snapshot();if(!s){ctx.restore();return;}
        binding=Object.values(role.expressions).find(b=>b.state===s.current);frame=s.frame;phase=s.phase;
        const f=binding.atlas_format,index=binding.clips[phase].atlas_offset+frame,img=images.get(binding.atlas);
        if(img)ctx.drawImage(img,index%f.columns*f.size,Math.floor(index/f.columns)*f.size,f.size,f.size,0,0,360,360);
      }else{
        const a=data.assets[binding.resource_id],img=images.get(a.atlas);frame=DeskBotStudioCore.frameAt({...a,fps:binding.fps,loop:binding.loop},paused?.55:seconds);
        if(img){const w=a.frame_width,h=a.frame_height,x=frame%a.columns*w,y=Math.floor(frame/a.columns)*h;
          if(binding.layout_id==='eye_anim')DeskBotMirrorRender.draw(ctx,img,x,y,w,h);
          else ctx.drawImage(img,x,y,w,h,Math.floor((360-w)/2),binding.layout_id==='listen_anim'?25:Math.floor((360-h)/2),w,h);
        }
      }
      ctx.restore();canvas.dataset.frame=String(frame);canvas.dataset.expression=binding.semantic;canvas.dataset.phase=phase;
      const label=document.getElementById('character-phase');if(label)label.textContent=binding.label+' · '+({entry:'进入',loop:'持续',exit:'退出'}[phase]);
    }
    function animate(now){raf=0;if(!visible()||document.hidden||paused)return;
      if(last){const delta=Math.min(now-last,250);seconds+=delta/1000;elapsed+=delta;while(elapsed>=50){scheduler?.advance();elapsed-=50;}}
      last=now;draw();raf=requestAnimationFrame(animate);
    }
    function start(){if(!raf&&!paused&&visible()&&!document.hidden&&currentReference())raf=requestAnimationFrame(animate);}
    function mount(){if(visible())paint();else stop();}
    function init(){
      document.addEventListener('input',e=>{
        if(!e.target.closest('#character-form')||busy)return;
        const {name,value}=e.target;if(['display_name','persona'].includes(name))draft[name]=value;
        else if(['provider','voice_id','speaking_style'].includes(name))draft.voice[name]=value;
        else if(name==='expression_role_id'){reference(value);resetPreview();}
        dirty=true;message='';failed=false;status();
        if(name==='expression_role_id'){document.querySelector('.characters-preview').outerHTML=previewView();preparePreview();}
      });
      document.addEventListener('submit',e=>{if(e.target.id==='character-form'){e.preventDefault();save();}});
      document.addEventListener('change',e=>{if(e.target.hasAttribute('data-character-import'))migrate(e.target.files[0]);});
      document.addEventListener('click',e=>{
        const b=e.target.closest('[data-character-open],[data-character-add],[data-character-semantic],[data-character-action]');if(!b||b.disabled)return;
        if(b.hasAttribute('data-character-open'))return choose(b.dataset.characterOpen);
        if(b.hasAttribute('data-character-add'))return add(b.dataset.characterAdd);
        if(b.hasAttribute('data-character-semantic')){
          semantic=b.dataset.characterSemantic;
          if(scheduler){if(paused)makeScheduler(selected());else scheduler.request(selected().expressions[semantic].state);}
          seconds=0;
          document.querySelectorAll('[data-character-semantic]').forEach(el=>el.setAttribute('aria-pressed',String(el===b)));draw();return;
        }
        switch(b.dataset.characterAction){
          case 'copy':save(true);break;
          case 'refresh':sync();break;
          case 'load-conflict':if(conflict&&!unsaved()){draft=copy(conflict);conflict=null;dirty=false;message='已载入新版';resetPreview();paint();}break;
          case 'export':exportDraft();break;
          case 'relink':reference(state.expression_catalog.roles.some(r=>r.id===draft.expression_role_id)?draft.expression_role_id:state.expression_catalog.roles[0].id);dirty=true;resetPreview();paint();break;
          case 'preview-retry':previewError='';previewMessage();preparePreview();break;
          case 'pause':paused=!paused;scheduler?.setPaused(paused);stop();b.textContent=paused?'播放预览':'暂停预览';draw();start();break;
        }
      });
      window.addEventListener('beforeunload',e=>{if(dirty||pendingSave){e.preventDefault();e.returnValue='';}});
      document.addEventListener('visibilitychange',()=>{stop();if(!document.hidden)start();});
      reduced.addEventListener('change',e=>{paused=e.matches;scheduler?.setPaused(paused);stop();const b=document.querySelector('[data-character-action="pause"]');if(b)b.textContent=paused?'播放预览':'暂停预览';start();});
      sync();
    }
    return Object.freeze({view,mount,init,state:()=>copy({state,draft,dirty,busy,message,failed,conflict,pendingSave,semantic,previewError,paused,playback:scheduler?.snapshot()||null})});
  }
  root.DeskBotCharacters=Object.freeze({create});
})(globalThis);
