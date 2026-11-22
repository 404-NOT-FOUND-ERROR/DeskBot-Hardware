/* Role intent analysis shares the existing authoring draft and device owner. */
(()=>{
  'use strict';
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const reasons={binding_missing:'先在节点中连接动画。',intent_expired:'这次分析已过期，点击重新分析。',session_required:'先连接 USB 并启用状态查询。',connection_changed:'设备连接已变化，请重新分析。',catalog_required:'先读取完整的设备目录。',catalog_session_changed:'目录来自上次会话，请重新读取。',semantic_not_installed:'设备尚未安装这个表情，请生成资源包。',resource_binding_changed:'草稿与当前资源不同，请先发布资源包。',layout_changed:'圆屏布局不同，请先核对资源包。',action_changed:'附带动作不同，请先核对资源包。',snapshot_review:'资源快照中已匹配；可继续电脑预览。',expression_interface_pending:'资源已匹配，等待接入这个表情的设备接口。',display_state_expired:'设备状态已过期，等待新状态后再分析。',device_not_ready:'桌宠正在处理交互，待机后再分析。',request_ready:'资源与当前会话已匹配，可以发送当前表情。'};
  const statuses={ready:'可以发送',planned:'电脑预演',needs_publish:'需要发布',unavailable:'下一步待完成',busy:'等待待机',stale:'重新核对'};
  function create(data,host){
    let semantic='neutral',source='saved_snapshot',result=null,error='',busy=false,token=0,analyzedKey='',analysisRequestId='',timer;
    const draft=()=>host.draft(),role=()=>draft().roles.find(r=>r.id===draft().selected_role_id);
    const key=()=>JSON.stringify([draft(),semantic,source]);
    function canPreview(entry,manifest){
      if(!entry)return false;
      const asset=data.assets[entry.resource_id],binding=data.semantics[semantic];
      if(!asset||entry.resource_sha256!==asset.deployed_sha256||entry.bytes!==asset.bytes||entry.layout_id!==binding.layout_id)return false;
      const candidate=asset.origin==='candidate';
      if(entry.fps!==(candidate?asset.fps:binding.fps)||entry.loop!==(candidate?asset.loop:binding.loop))return false;
      const layout=manifest.entries.find(r=>r.kind==='layout'&&r.id===entry.layout_id),reference=data.installed_directory.manifest.entries.find(r=>r.kind==='layout'&&r.id===entry.layout_id);
      return layout&&reference&&Object.keys(reference).every(k=>layout[k]===reference[k]);
    }
    function view(){return `<section class="surface e3e-planner" aria-label="角色表达链路"><div class="e3d2-heading"><div><div class="eyebrow">ROLE → EXPRESSION</div><h2>从角色，看到它的表达。</h2><p>沿用节点中的接线，核对资源和设备接口。</p></div><span class="e3d-tag" id="e3e-status">电脑预演</span></div><div class="e3e-toolbar"><label>核对来源<select id="e3e-source"><option value="saved_snapshot" ${source==='saved_snapshot'?'selected':''}>E1.18 资源快照</option><option value="device" ${source==='device'?'selected':''}>USB 实时目录</option></select></label><label>表达语义<select id="e3e-semantic">${Object.entries(data.semantics).map(([id,r])=>`<option value="${esc(id)}" ${id===semantic?'selected':''}>${esc(r.label)} · ${esc(id)}</option>`).join('')}</select></label><button class="button button-primary" data-e3e-analyze>分析当前接线</button></div><div class="e3e-chain" id="e3e-chain"></div><div class="e3e-previews" id="e3e-previews"></div><p id="e3e-message" role="status">选择语义后，点击分析当前接线。</p><div class="publish-actions"><button class="button" data-e3e-edit>在节点中编辑</button><button class="button button-primary" data-e3e-send hidden>发送一次平静表情</button></div></section>`;}
    function paint(){
      const slot=document.querySelector('.e3e-planner');if(!slot)return;
      if(result&&analyzedKey!==key()){result=null;error='草稿已更新，点击重新分析。';}
      const v=host.control()?.device;
      if(result?.command&&Object.keys(result.command).filter(k=>['connection_id','target_id','boot_id','session_id'].includes(k)).some(k=>result.command[k]!==v?.[k])){result=null;error='设备已重连，点击重新分析。';}
      if(result?.source==='device'&&result.catalog_id&&(v?.catalog?.status!=='complete'||v.catalog.manifest?.catalog_id!==result.catalog_id)){result=null;error='设备目录已更新，点击重新分析。';}
      const currentRole=role(),resource=currentRole.bindings[semantic];
      slot.querySelector('#e3e-status').textContent=result?statuses[result.status]:source==='device'?'设备核对':'电脑预演';
      slot.querySelector('#e3e-message').textContent=error||(result?reasons[result.reason]||result.reason:'选择语义后，点击分析当前接线。');
      const chain=slot.querySelector('#e3e-chain'),chainKey=JSON.stringify([currentRole.id,currentRole.name,semantic,resource,result?.status]);
      if(chain.dataset.key!==chainKey){chain.dataset.key=chainKey;chain.innerHTML=[['角色',currentRole.name],['表达',data.semantics[semantic].label],['资源',resource||'等待连接'],['圆屏',result?statuses[result.status]:'等待分析']].map(([label,value],i)=>`${i?'<i aria-hidden="true"></i>':''}<div><small>${esc(label)}</small><strong>${esc(value)}</strong></div>`).join('');}
      const previews=slot.querySelector('#e3e-previews'),previewKey=JSON.stringify([semantic,source,resource,result?.installed_resource_id,result?.catalog_id]);
      if(previews.dataset.key!==previewKey){previews.dataset.key=previewKey;const live=source==='device'&&result?.catalog_id,before=live?result.installed_resource_id:data.semantics[semantic].resource_id,entry=live?v?.catalog?.manifest?.entries.find(r=>r.id===semantic&&(r.kind==='expression'||r.kind==='status')):null,known=!live||canPreview(entry,v.catalog.manifest);previews.innerHTML=`<div><span>${live?'设备当前资源':'E1.18 资源快照'}</span>${known?host.canvas(before,{animate:true,cls:'screen',semanticId:semantic}):'<p>当前资源或播放参数需导入预览</p>'}<small>${esc(before||'未安装')}</small></div><div><span>当前角色草稿</span>${host.canvas(resource,{animate:true,cls:'screen',semanticId:semantic})}<small>${esc(resource||'等待连接')}</small></div>`;host.paint();}
      const analyze=slot.querySelector('[data-e3e-analyze]');analyze.disabled=busy;analyze.textContent=busy?'正在分析…':'分析当前接线';
      const send=slot.querySelector('[data-e3e-send]');send.hidden=!result?.command;send.disabled=busy||!v?.can_submit||!(v?.display?.expression_semantics??['neutral']).includes(semantic);send.textContent=`发送一次${data.semantics[semantic].label}表情`;
    }
    async function analyze(){
      if(busy)return;busy=true;error='';result=null;const generation=++token,analysisKey=key();paint();
      const v=host.control()?.device,context=v?Object.fromEntries(['connection_id','target_id','boot_id','session_id'].map(k=>[k,v[k]])):null;
      const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),8000);
      try{
        analysisRequestId='analysis-'+crypto.randomUUID();
        const response=await fetch('/api/role-expression/resolve',{method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Studio':'e3e'},signal:controller.signal,body:JSON.stringify({draft:draft(),semantic,source,context,request_id:analysisRequestId})});
        const row=await response.json();if(!response.ok)throw new Error(row.error||'分析未完成');
        if(generation===token&&analysisKey===key()){result=row;analyzedKey=analysisKey;}
      }catch(e){error=e.name==='AbortError'?'分析超时，请重试。':e.message;}
      finally{clearTimeout(timeout);busy=false;paint();}
    }
    async function send(){
      paint();if(!result?.command||busy)return;
      const command=result.command,analysisKey=key(),catalogId=result.catalog_id;
      busy=true;error='';paint();
      const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),8000);
      try{
        const context=Object.fromEntries(['connection_id','target_id','boot_id','session_id'].map(k=>[k,command[k]]));
        const check=await fetch('/api/role-expression/resolve',{method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Studio':'e3e'},signal:controller.signal,body:JSON.stringify({draft:draft(),semantic,source,context,request_id:analysisRequestId})});
        const fresh=await check.json();if(!check.ok)throw new Error(fresh.error||'重新核对未完成');
        paint();if(analysisKey!==key()||!result||catalogId!==fresh.catalog_id||fresh.command?.command_id!==command.command_id){result=null;error='接线或设备状态已变化，请重新分析。';return;}
        result=fresh;
        const response=await fetch('/api/studio/command',{method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Studio':'e3c6'},signal:controller.signal,body:JSON.stringify(command)});
        const row=await response.json();if(!response.ok)throw new Error(row.error||'发送未完成');
        error='已交给设备连接，执行进度见上方回执。';
      }catch(e){error=e.name==='AbortError'?'发送超时，请查看上方回执。':e.message;}finally{clearTimeout(timeout);busy=false;paint();}
    }
    function init(){
      document.addEventListener('change',e=>{if(e.target.id==='e3e-source'||e.target.id==='e3e-semantic'){source=document.getElementById('e3e-source').value;semantic=document.getElementById('e3e-semantic').value;result=null;error='';token++;paint();}});
      document.addEventListener('click',e=>{if(e.target.closest('[data-e3e-analyze]'))void analyze();else if(e.target.closest('[data-e3e-send]'))void send();else if(e.target.closest('[data-e3e-edit]')){const button=document.createElement('button');button.dataset.action='edit-semantic';button.dataset.semantic=semantic;document.body.append(button);button.click();button.remove();}});
      paint();timer=setInterval(paint,400);window.addEventListener('pagehide',()=>clearInterval(timer));
    }
    return Object.freeze({view,init,state:()=>({semantic,source,result:result?JSON.parse(JSON.stringify(result)):null,busy,error})});
  }
  window.DeskBotStudioE3EUI=Object.freeze({create});
})();
