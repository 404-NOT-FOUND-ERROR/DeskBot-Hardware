'use strict';
(()=>{
 const $=s=>document.querySelector(s),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 let state=null,project=null,selected='happy',player=null,previewKey='',busy=false,scale=.8,pan={x:20,y:20},drag=null,last=0,toastTimer,pendingAsset=null,epoch=0;
 if(!$('#recover')){const b=document.createElement('button');b.id='recover';b.hidden=true;b.textContent='恢复安装前版本';$('#install').after(b);}
 if(!$('#rollback')){const b=document.createElement('button');b.id='rollback';b.hidden=true;b.textContent='恢复上一版';$('#recover').after(b);}
 const target=n=>state.catalog.find(c=>c.id===n)?.alias||n;
 async function api(action,value){const r=await fetch('/api/expressions/'+action,{cache:'no-store',...(value?{method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Studio':'expression-pipeline-v1'},body:JSON.stringify(value)}:{})});const d=await r.json();if(!r.ok)throw Error(d.error||'处理未完成');return d;}
 function toast(text){$('#toast').textContent=text;$('#toast').classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.remove('visible'),4500);}
 async function task(fn){if(busy)return;epoch++;busy=true;paintActions();try{await fn();await refresh();}catch(e){toast(e.message);}finally{busy=false;paintActions();}}
 function file(name){return '/api/expressions/file?'+new URLSearchParams({project:project.id,revision:project.build.revision,name});}
 function transform(){ $('#world').style.transform=`translate(${pan.x}px,${pan.y}px) scale(${scale})`;$('#zoom').textContent=Math.round(scale*100)+'%'; }
 function select(name){selected=name;paintGraph();paintInspector();if(player){player.request(selected,true);}}
 function paintGraph(){if(!state)return;const query=$('#search').value.trim().toLowerCase(),catalog=state.catalog.filter(c=>c.id!=='neutral'&&c.id!=='sleepy').filter(c=>(c.label+c.id+c.trigger).toLowerCase().includes(query));
  let html='',paths='';const node=(x,y,kind,label,note,id,icon)=>`<button class="node ${kind} ${id===selected?'selected':''}" data-semantic="${esc(id)}" style="left:${x}px;top:${y}px"><span class="icon">${icon}</span><span><b>${esc(label)}</b><small>${esc(note)}</small></span></button>`;
  catalog.forEach((c,i)=>{const y=48+i*94,b=project?.bindings[target(c.id)],a=project?.assets.find(a=>a.id===b?.asset);html+=node(26,y,'trigger',c.trigger,'保留现有事件入口',c.id,'⌁')+node(282,y,'semantic',c.label,c.id+(c.loop?' · 持续':' · 单次'),c.id,'◡')+node(538,y,'asset'+(b?' changed':''),a?.name||'当前角色素材',b?`新素材 · 镜像${b.mirror?'开启':'关闭'}`:`DFM1 · ${(c.duration_ms/1000).toFixed(2)} 秒`,c.id,b?'✦':'◉');
    for(const [x1,x2] of [[220,282],[476,538]])paths+=`<path class="${selected===c.id?'active':''}" d="M${x1},${y+35} C${x1+30},${y+35} ${x2-30},${y+35} ${x2},${y+35}"/>`;
    if(selected===c.id)paths+=`<path class="active" d="M732,${y+35} C820,${y+35} 795,300 864,300"/>`;
  });html+=node(864,265,'output','360 圆屏资源包',project?.build?.status==='ready'?'已构建 · 可下载安装':'等待构建',selected,'▧');
  $('#nodes').innerHTML=html;$('#edges').innerHTML=paths;$('#world').style.height=Math.max(600,catalog.length*94+100)+'px';$('#edges').style.height=$('#world').style.height;
  $('#nodes').querySelectorAll('[data-semantic]').forEach(b=>b.onclick=()=>select(b.dataset.semantic));transform();
 }
 function paintInspector(){if(!state)return;const c=state.catalog.find(c=>c.id===selected),b=project?.bindings[target(selected)];$('#semantic-id').textContent=selected;$('#semantic-label').textContent=c?.label||selected;$('#binding-tag').textContent=b?'新素材绑定':'当前素材';
  $('#assets').innerHTML='<option value="">保留当前素材</option>'+(project?.assets||[]).map(a=>`<option value="${a.id}">${esc(a.name)}</option>`).join('');$('#assets').value=pendingAsset&&pendingAsset.project===project?.id&&pendingAsset.semantic===selected?pendingAsset.id:b?.asset||'';$('#mirror').checked=b?.mirror??c?.mirror??false;$('#fps').value=b?.fps||25;
  $('#alias-note').textContent=['tried','sleepy'].includes(selected)?'困倦与疲惫共用这份素材，持续和单次播放规则各自保留。':selected==='idle'?'待机与平静共用素材。':'';assetInfo();
 }
 function assetInfo(){const a=project?.assets.find(a=>a.id===$('#assets').value);$('#asset-info').textContent=a?`${a.source_width} × ${a.source_height} · ${a.source_frames} 源帧 · ${(a.source_duration_ms/1000).toFixed(2)} 秒 → 360 圆屏`:'选择素材后保存绑定，再生成同包预览。';$('#fps').disabled=a?.suffix!=='.eaf';}
 function paintActions(){const running=['running','recovery_required'].includes(state?.job?.status),build=project?.build,current=project?.installation?.status==='installed'&&project.installation.run===state?.baseline_run;$('#build').disabled=busy||running||!Object.keys(project?.bindings||{}).length;$('#bind').disabled=busy||running;$('#upload').disabled=busy||running;$('#install').disabled=busy||running||!build||build.installed;$('#recover').hidden=state?.job?.status!=='recovery_required';$('#rollback').hidden=!current||running;$('#rollback').disabled=busy;$('#download').hidden=!build;if(build)$('#download').href=file('deskbot-expression.zip');}
 function paintDelivery(){const job=state.job?.project===project?.id?state.job:null,build=project?.build;
  if(job){$('#pipeline').innerHTML=job.stages.map(s=>`<li>✓ ${esc(s.name)}</li>`).join('')+(job.status==='running'?'<li>正在处理…</li>':'')+(job.error?`<li class="failed">${esc(job.error)}</li>`:'');}
  else $('#pipeline').innerHTML='<li>'+(build?'✓ 安装包已生成':'上传素材 → 保存绑定 → 生成安装包')+'</li>';
  if(build){let r=build.report;$('#capacity-bar').style.width=(r.package_bytes/8388608*100)+'%';$('#build-result').textContent=`${(r.package_bytes/1048576).toFixed(2)} / 8 MiB · 剩余 ${(r.partition_free_bytes/1048576).toFixed(2)} MiB · 生产播放器核验通过 · 复用当前配套固件`;$('#details').textContent=JSON.stringify({revision:build.revision,graph_sha256:r.graph_sha256,app_sha256:build.manifest.app_sha256,source_bindings:project.bindings,steps:job?.stages},null,2);}
  else{$('#capacity-bar').style.width='0';$('#build-result').textContent='保存绑定后生成新的资源包与同包预览。';$('#details').textContent='';}
  $('#installation-result').textContent=project?.installation?(project.installation.status==='installed'?'设备安装、回读与启动核验通过；圆屏观察待反馈。':project.installation.message||project.installation.status):'';paintActions();
 }
 async function preview(){let key=project?.build?project.id+':'+project.build.revision:'baseline:'+state.baseline_graph_sha256;if(key===previewKey)return;
  const url=project?.build?file('face-motion.dfm'):'/layered-face/assets-8m.bin';let bytes;
  if(project?.build)bytes=new Uint8Array(await (await fetch(url)).arrayBuffer());
  else{const text=await (await fetch('/layered-face/')).text();const match=text.match(/atob\('([A-Za-z0-9+/=]+)'\)/);if(!match)throw Error('当前资源预览尚未就绪');bytes=Uint8Array.from(atob(match[1]),c=>c.charCodeAt(0));}
  if((project?.build?project.id+':'+project.build.revision:'baseline:'+state.baseline_graph_sha256)!==key)return;
  player=new DeskBotLayeredFace(bytes);player.request(selected,true);previewKey=key;$('#preview-tag').textContent=project?.build?'已编译资源 · 与安装包相同':'当前已安装素材 · 新绑定生成后可预览';
 }
 async function refresh(next){const started=epoch,value=next||await api('state');if(started!==epoch)return;state=value;let id=project?.id||localStorage.getItem('deskbot-expression-project');project=state.projects.find(p=>p.id===id)||state.projects[0]||null;
  $('#projects').innerHTML=state.projects.map(p=>`<option value="${p.id}">${esc(p.name)} · r${p.revision}</option>`).join('');if(project){$('#projects').value=project.id;localStorage.setItem('deskbot-expression-project',project.id);}$('#connection').textContent=state.image_processing_ready?`360 圆屏 · 当前版本 ${state.baseline_graph_sha256.slice(0,8)}`:'图像处理依赖尚未就绪';
  paintGraph();paintInspector();paintDelivery();await preview();
 }
 $('#new-project').onclick=()=>task(async()=>{project=await api('create',{name:'表情方案 '+(state.projects.length+1)});previewKey='';toast('方案已创建，选择要替换的表情并上传素材。');});
 $('#projects').onchange=()=>task(async()=>{project=state.projects.find(p=>p.id===$('#projects').value);previewKey='';});
 $('#upload').onchange=()=>task(async()=>{if(!project)project=await api('create',{name:'我的表情方案'});const f=$('#upload').files[0];if(!f)return;const r=await fetch('/api/expressions/upload?'+new URLSearchParams({project:project.id,revision:project.revision,name:f.name}),{method:'POST',headers:{'X-DeskBot-Studio':'expression-pipeline-v1'},body:f});const p=await r.json();if(!r.ok)throw Error(p.error);project=p;pendingAsset={project:p.id,semantic:selected,id:p.assets.at(-1).id};toast('素材已解码，点击保存素材绑定即可接入。');$('#upload').value='';});
 $('#assets').onchange=assetInfo;
 $('#bind').onclick=()=>task(async()=>{if(!project)throw Error('请先上传素材或新建方案');project=await api('bind',{ident:project.id,revision:project.revision,semantic:selected,asset:$('#assets').value||null,mirror:$('#mirror').checked,fps:Number($('#fps').value)});pendingAsset=null;toast('真实素材绑定已保存。');});
 $('#build').onclick=()=>task(async()=>{await api('build',{ident:project.id,revision:project.revision});toast('开始解码、编译和生产播放器核验。');});
 $('#install').onclick=()=>$('#install-dialog').showModal();$('#install-cancel').onclick=()=>$('#install-dialog').close();
 $('#install-confirm').onclick=()=>task(async()=>{$('#install-dialog').close();await api('install',{ident:project.id,revision:project.revision,confirmed:true});toast('正在保存当前版本并安装。');});
 $('#recover').onclick=()=>task(async()=>{if(!window.confirm('将恢复本次安装前保存的 App 和表情资源，保留当前 NVS。设备会重新启动，完成回读与启动核验后再恢复日常后台。'))return;await api('recover',{confirmed:true});toast('正在恢复安装前的完整版本。');});
 $('#rollback').onclick=()=>task(async()=>{if(!window.confirm('恢复这个方案安装前的表情版本？设备会重新启动，当前 NVS 会保留。'))return;await api('rollback',{ident:project.id,confirmed:true});toast('正在恢复上一版。');});
 $('#search').oninput=paintGraph;$('#replay').onclick=()=>player?.request(selected,true);$('#cancel').onclick=()=>player?.cancel();
 $('#zoom-in').onclick=()=>{scale=Math.min(1.4,scale+.1);transform();};$('#zoom-out').onclick=()=>{scale=Math.max(.35,scale-.1);transform();};$('#fit').onclick=()=>{scale=Math.min(1,($('#viewport').clientWidth-25)/1090);pan={x:14,y:18};transform();};
 $('#viewport').onpointerdown=e=>{if(e.target.closest('button'))return;drag={x:e.clientX,y:e.clientY,px:pan.x,py:pan.y};$('#viewport').setPointerCapture(e.pointerId);};$('#viewport').onpointermove=e=>{if(!drag)return;pan={x:drag.px+e.clientX-drag.x,y:drag.py+e.clientY-drag.y};transform();};$('#viewport').onpointerup=()=>{drag=null;};$('#viewport').onpointercancel=()=>{drag=null;};$('#viewport').addEventListener('wheel',e=>{e.preventDefault();if(e.ctrlKey)scale=Math.max(.35,Math.min(1.4,scale-e.deltaY*.001));else pan.y-=e.deltaY;transform();},{passive:false});
 function tick(t){if(player&&t-last>=40){const elapsed=last?t-last:40;last=t;player.advance(Math.min(elapsed,100));$('#face').getContext('2d').drawImage(player.frame(),0,0);$('#phase').textContent={entry:'进入',body:'完整表演',exit:'收尾',idle:'待机'}[player.phase];document.querySelectorAll('[data-phase]').forEach(el=>el.classList.toggle('active',el.dataset.phase===player.phase));}requestAnimationFrame(tick);}requestAnimationFrame(tick);
 async function poll(){try{if(!busy){const started=epoch,old=JSON.stringify(state?.job),next=await api('state');if(!busy&&started===epoch&&(old!==JSON.stringify(next.job)||!state)){await refresh(next);}}}catch(e){toast(e.message);}setTimeout(poll,1300);}
 refresh().then(()=>{$('#fit').click();poll();}).catch(e=>toast(e.message));
})();
