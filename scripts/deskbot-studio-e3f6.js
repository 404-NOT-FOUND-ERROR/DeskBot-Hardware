/* Role drafts use reviewed asset layouts; playback shares the live-control owner. */
(()=>{
  'use strict';
  const keys=['connection_id','target_id','boot_id','session_id'];
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const reasons={binding_missing:'先在节点中连接动画。',intent_expired:'点击重新核对。',session_required:'连接 USB 并启用状态查询。',connection_changed:'设备已重连，重新核对接线。',catalog_required:'读取完整设备目录后播放。',catalog_session_changed:'重连后重新读取目录。',semantic_not_installed:'先发布这个表情的资源。',resource_binding_changed:'草稿与当前资源不同，先检查发布内容。',layout_changed:'核对素材布局后重新发布。',action_changed:'核对附带动作后重新发布。',snapshot_review:'E1.45 留存目录已匹配，可以电脑预览。',expression_interface_pending:'资源已匹配，等待接入该表情的播放接口。',display_state_expired:'点击播放，读取最新状态后发送一次。',device_not_ready:'回到主页待机后播放。',request_ready:'接线已匹配，可发送一次当前表情。'};
  const statuses={ready:'可以发送',planned:'电脑预演',needs_publish:'需要发布',unavailable:'待完成',busy:'等待待机',stale:'等待新状态'};
  const layouts={eye_anim:'双眼镜像',full_frame:'完整画面 · 无镜像',listen_anim:'聆听布局'};
  function create(data,host){
    let semantic='neutral',source='saved_snapshot',result=null,error='',busy=false,token=0,acceptedKey='',timer;
    const draft=()=>host.draft(),role=()=>draft().roles.find(r=>r.id===draft().selected_role_id);
    const context=v=>v?Object.fromEntries(keys.map(k=>[k,v[k]])):null;
    const key=()=>JSON.stringify([draft(),semantic,source]);
    const layout=id=>data.asset_layouts[id]|| (data.assets[id]?.object_kind==='listen'?'listen_anim':'eye_anim');
    const reference=data.role_reference;
    function canPreview(entry,manifest){
      if(!entry)return false;
      const asset=data.assets[entry.resource_id],binding=data.semantics[semantic];
      if(!asset||entry.resource_sha256!==asset.deployed_sha256||entry.bytes!==asset.bytes||entry.layout_id!==layout(entry.resource_id))return false;
      const candidate=asset.origin==='candidate'&&entry.resource_id!==binding.resource_id;
      if(entry.fps!==(candidate?asset.fps:binding.fps)||entry.loop!==(candidate?asset.loop:binding.loop)||entry.implicit_action_id!==binding.action_id)return false;
      const actual=manifest.entries.find(r=>r.kind==='layout'&&r.id===entry.layout_id),expected=data.layout_profiles[entry.layout_id];
      return actual&&expected&&JSON.stringify(Object.entries(actual).sort())===JSON.stringify(Object.entries(expected).sort());
    }
    function view(){return `<section class="surface e3e-planner" aria-label="角色表达链路"><div class="e3d2-heading"><div><div class="eyebrow">ROLE → EXPRESSION</div><h2>从角色，看到它的表达。</h2><p>接线、素材布局与设备目录一起核对。</p></div><span class="e3d-tag" id="e3e-status">电脑预演</span></div><div class="e3e-toolbar"><label>核对来源<select id="e3e-source"><option value="saved_snapshot" ${source==='saved_snapshot'?'selected':''}>E1.45 留存目录 · 37 项</option><option value="device" ${source==='device'?'selected':''}>USB 实时目录</option></select></label><label>表达语义<select id="e3e-semantic">${Object.entries(data.semantics).map(([id,r])=>`<option value="${esc(id)}" ${id===semantic?'selected':''}>${esc(r.label)} · ${esc(id)}</option>`).join('')}</select></label><button class="button button-primary" data-e3e-analyze>分析当前接线</button></div><div class="e3e-chain" id="e3e-chain"></div><div class="e3e-previews" id="e3e-previews"></div><p id="e3e-message" role="status">选择语义，核对当前接线。</p><div class="publish-actions"><button class="button" data-e3e-edit>在节点中编辑</button><button class="button button-primary" data-e3e-send hidden>播放当前表情</button><button class="button" data-e3f6-cancel hidden>取消等待</button></div></section>`;}
    function paint(){
      const slot=document.querySelector('.e3e-planner'),v=host.control()?.device;
      if(result&&acceptedKey!==key()){result=null;error='草稿已更新，重新核对接线。';}
      if(result?.source==='device'&&(JSON.stringify(result.context)!==JSON.stringify(context(v))||result.catalog_id!==v?.catalog?.manifest?.catalog_id)){
        result=null;error='设备或目录已更新，重新核对接线。';
      }
      const nodeMessage=document.getElementById('e3f6-node-result');
      if(nodeMessage)nodeMessage.textContent=host.selectedSemantic()===semantic?(busy?'核对最新状态后播放一次…':error):'';
      const nodeButton=document.querySelector('[data-e3f6-node]');if(nodeButton)nodeButton.disabled=busy||host.control()?.busy;
      if(!slot)return;
      const resource=role().bindings[semantic],manifest=source==='device'?v?.catalog?.manifest:reference;
      slot.querySelector('#e3e-source').value=source;slot.querySelector('#e3e-semantic').value=semantic;
      slot.querySelector('#e3e-status').textContent=busy?'正在核对':result?statuses[result.status]:'等待分析';
      slot.querySelector('#e3e-message').textContent=error||(result?reasons[result.reason]||result.reason:'选择语义，核对当前接线。');
      const chain=slot.querySelector('#e3e-chain'),chainKey=JSON.stringify([role().id,role().name,semantic,resource,result?.status]);
      if(chain.dataset.key!==chainKey){chain.dataset.key=chainKey;chain.innerHTML=[['角色',role().name],['表达',data.semantics[semantic].label],['资源',resource||'等待连接'],['布局',layouts[layout(resource)]]].map(([label,value],i)=>`${i?'<i aria-hidden="true"></i>':''}<div><small>${esc(label)}</small><strong>${esc(value)}</strong></div>`).join('');}
      const before=manifest?.entries.find(r=>r.id===semantic&&(r.kind==='expression'||r.kind==='status'));
      const previews=slot.querySelector('#e3e-previews'),previewKey=JSON.stringify([source,semantic,resource,before,manifest?.catalog_id]);
      if(previews.dataset.key!==previewKey){previews.dataset.key=previewKey;previews.innerHTML=`<div><span>${source==='device'?'设备当前资源':'E1.45 留存目录'}</span>${canPreview(before,manifest)?host.canvas(before.resource_id,{animate:true,cls:'screen',semanticId:semantic,layout:before.layout_id}):'<p>读取并核对当前资源后显示预览</p>'}<small>${esc(before?.resource_id||'等待目录')}</small></div><div><span>当前角色草稿 · ${esc(layouts[layout(resource)])}</span>${host.canvas(resource,{animate:true,cls:'screen',semanticId:semantic,layout:layout(resource)})}<small>${esc(resource||'等待连接')}</small></div>`;host.paint();}
      const analyze=slot.querySelector('[data-e3e-analyze]');analyze.disabled=busy;analyze.textContent=busy?'正在核对…':'分析当前接线';
      const send=slot.querySelector('[data-e3e-send]'),eligible=source==='device'&&result&&['request_ready','display_state_expired','device_not_ready'].includes(result.reason);
      send.hidden=!eligible;send.disabled=busy||host.control()?.busy;send.textContent=`播放一次${data.semantics[semantic].label}`;
      slot.querySelector('[data-e3f6-cancel]').hidden=!busy;
    }
    async function resolve(requestId,signal){
      const v=host.control()?.device;
      const response=await fetch('/api/role-expression/resolve',{method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Studio':'e3e'},signal,
        body:JSON.stringify({draft:draft(),semantic,source,context:source==='device'?context(v):null,request_id:requestId})});
      const value=await response.json();if(!response.ok)throw Error(value.error||'接线核对未完成');
      return {...value,context:source==='device'?context(v):null};
    }
    async function analyze(){
      if(busy)return;busy=true;error='';result=null;const version=++token,selected=key(),abort=new AbortController(),timeout=setTimeout(()=>abort.abort(),8000);paint();
      try{const next=await resolve('analysis-'+crypto.randomUUID(),abort.signal);if(token===version&&key()===selected){result=next;acceptedKey=selected;}}
      catch(e){if(version===token)error=e.name==='AbortError'?'核对超时，请重试。':e.message;}
      finally{clearTimeout(timeout);busy=false;paint();}
    }
    async function send(nodeSemantic){
      if(busy)return;
      if(nodeSemantic){semantic=nodeSemantic;source='device';result=null;}
      else {paint();if(source!=='device'||!result||!['request_ready','display_state_expired','device_not_ready'].includes(result.reason))return;}
      const selected=key(),version=++token,panel=nodeSemantic?document.getElementById('graph-world'):document.querySelector('.e3e-planner'),hash=location.hash;
      // A new click gets a new intent; retries within that click never resend a command.
      const requestId='node-'+crypto.randomUUID(),abort=new AbortController(),timeout=setTimeout(()=>abort.abort(),16000);
      busy=true;error='';paint();
      const current=()=>token===version&&key()===selected&&location.hash===hash&&panel?.isConnected&&(!nodeSemantic||host.selectedSemantic()===semantic);
      try{
        await host.playRole({operation:semantic,isCurrent:current,resolve:async()=>{
          const next=await resolve(requestId,abort.signal);
          if(!current())throw Error('接线或页面已变化，已结束本次等待。');
          if(!next.command)throw Error(reasons[next.reason]||next.reason);
          result=next;acceptedKey=selected;return next;
        }});
        if(current())error='已交给设备连接，执行进度见设备回执。';
      }catch(e){if(version===token)error=e.name==='AbortError'?'请求超时，请查看设备回执。':e.message;}
      finally{clearTimeout(timeout);busy=false;paint();}
    }
    function init(){
      document.addEventListener('change',e=>{if(['e3e-source','e3e-semantic'].includes(e.target.id)){source=document.getElementById('e3e-source').value;semantic=document.getElementById('e3e-semantic').value;result=null;error='';token++;paint();}});
      document.addEventListener('click',e=>{
        if(e.target.closest('[data-e3e-analyze]'))void analyze();
        else if(e.target.closest('[data-e3e-send]'))void send();
        else if(e.target.closest('[data-e3f6-node]'))void send(host.selectedSemantic());
        else if(e.target.closest('[data-e3f6-cancel]')){token++;error='已取消本次等待。';paint();}
        else if(e.target.closest('[data-e3e-edit]')){const b=document.createElement('button');b.dataset.action='edit-semantic';b.dataset.semantic=semantic;document.body.append(b);b.click();b.remove();}
      });
      paint();timer=setInterval(paint,250);window.addEventListener('pagehide',()=>{token++;clearInterval(timer);});
    }
    return Object.freeze({view,init,state:()=>({semantic,source,result:result?JSON.parse(JSON.stringify(result)):null,busy,error})});
  }
  window.DeskBotStudioE3EUI=Object.freeze({create});
})();
