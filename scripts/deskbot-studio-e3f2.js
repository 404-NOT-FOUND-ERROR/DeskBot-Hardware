/* A whole-role view; analysis and node navigation share the existing draft. */
(()=>{
  'use strict';
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels={ready:'可以请求',matched:'资源已匹配',needs_publish:'需要发布',missing:'待接线',interface_pending:'待接播放接口',state_managed:'随语音状态播放',waiting:'待重新核对'};
  const nextSteps={binding_missing:'进入节点连接动画',intent_expired:'重新核对当前角色',session_required:'连接 USB 并启用状态查询',connection_changed:'连接已更新，重新核对',catalog_required:'读取完整设备目录',catalog_session_changed:'重连后重新读取目录',semantic_not_installed:'生成并发布选定资源',resource_binding_changed:'在发布页检查替换与容量',layout_changed:'核对布局后重新发布',action_changed:'核对附带动作后重新发布',snapshot_review:'留存资源已对应，可预览',expression_interface_pending:'资源已对应，后续接入播放接口',display_state_expired:'等待新状态后重新核对',device_not_ready:'待机后重新核对',request_ready:'在对照预览中核对并发送',voice_state_managed:'由语音状态切换'};
  const actionLabels={shark_head:'摇头',shark_head_decay:'轻轻摇头',cat_nuzzle:'蹭蹭',beat_swing:'节奏摇摆',look_around:'好奇环顾'};
  function create(data,host){
    let source='saved_snapshot',filter='all',report=null,error='',busy=false,generation=0,acceptedKey='',timer;
    const draft=()=>host.draft(),role=()=>draft().roles.find(r=>r.id===draft().selected_role_id);
    const device=()=>host.control()?.device;
    const connection=()=>{
      const v=device();
      return v?Object.fromEntries(['connection_id','target_id','boot_id','session_id'].map(k=>[k,v[k]??null])):null;
    };
    const currentKey=()=>{
      const v=device();
      return JSON.stringify([draft(),source,source==='device'?[connection(),v?.enabled,v?.catalog?.status,
        v?.catalog?.target_id,v?.catalog?.boot_id,v?.catalog?.session_id,v?.catalog?.manifest,
        v?.display?.expression_semantics,v?.can_submit,Number.isInteger(v?.age_ms)&&v.age_ms>=0&&v.age_ms<2000]:null]);
    };
    function view(){return `<section class="surface e3f2-overview" aria-label="角色表达总览"><div class="e3d2-heading"><div><div class="eyebrow">ROLE OVERVIEW</div><h2>一个角色，全部表达。</h2><p>先看接线与资源，再进入节点预览或发布。</p></div><span class="e3d-tag">28 个表情 · 1 个聆听状态</span></div><div class="e3f2-toolbar"><label>核对来源<select id="e3f2-source"><option value="saved_snapshot">E1.18 留存快照</option><option value="device">USB 实时目录</option></select></label><button class="button button-primary" data-e3f2-refresh>核对整个角色</button><div class="e3f2-filters" role="group" aria-label="筛选表达"><button class="button" data-e3f2-filter="all" aria-pressed="true">全部</button><button class="button" data-e3f2-filter="attention" aria-pressed="false">待处理</button><button class="button" data-e3f2-filter="matched" aria-pressed="false">已匹配</button></div></div><p id="e3f2-message" role="status">当前角色：${esc(role().name)}。点击核对，查看全部表达。</p><div id="e3f2-summary" class="e3f2-summary"></div><div id="e3f2-rows" class="e3f2-rows"></div></section>`;}
    function paint(){
      const panel=document.querySelector('.e3f2-overview');if(!panel)return;
      if(report&&acceptedKey!==currentKey()){report=null;error='角色、接线或设备状态已更新，请重新核对。';}
      panel.querySelector('#e3f2-source').value=source;
      const refresh=panel.querySelector('[data-e3f2-refresh]');refresh.disabled=busy;refresh.textContent=busy?'正在核对…':'核对整个角色';
      panel.querySelectorAll('[data-e3f2-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.e3f2Filter===filter)));
      panel.querySelector('#e3f2-message').textContent=error||(report?`${report.role_name} · ${source==='device'?'USB 实时目录':'E1.18 留存快照'} · 点击一项查看预览与接线。`:`当前角色：${role().name}。点击核对，查看全部表达。`);
      const summary=panel.querySelector('#e3f2-summary'),rows=panel.querySelector('#e3f2-rows');
      const renderKey=JSON.stringify([report,filter]);if(rows.dataset.key===renderKey)return;rows.dataset.key=renderKey;
      if(!report){summary.innerHTML='';rows.innerHTML='';return;}
      summary.innerHTML=[['语义',report.total],['资源匹配',report.resource_matches],['需要发布',report.counts.needs_publish],['待接线',report.counts.missing]].map(([label,value])=>`<div><strong>${value}</strong><span>${label}</span></div>`).join('');
      const selected=report.entries.filter(r=>filter==='all'||(filter==='matched'?r.resource_matches:!['ready','matched','state_managed'].includes(r.status)));
      rows.innerHTML=selected.length?selected.map(r=>`<article class="e3f2-row" data-semantic="${esc(r.semantic_id)}" data-status="${r.status}"><div class="e3f2-name"><strong>${esc(r.label)}</strong><small>${esc(r.semantic_id)}</small><span class="e3f2-badge">${labels[r.status]}</span><small>${esc(nextSteps[r.reason]||'重新核对当前状态')}</small></div><div class="e3f2-resource"><small>角色接线</small><code>${esc(r.resource_id||'等待连接')}</code><small>核对资源 · ${esc(r.installed_resource_id||'等待目录')}</small></div><div class="e3f2-playback">${r.playback?`${r.playback.fps} fps · ${r.playback.loop?'循环':'单次'}<small>${r.playback.layout_id==='listen_anim'?'聆听布局':'眼部布局'}</small>`:'连接后显示播放方式'}<small>${r.implicit_action_id?`附带${esc(actionLabels[r.implicit_action_id]||r.implicit_action_id)} · 底座完成事实单独显示`:'独立动画'}</small></div><div class="e3f2-row-actions"><button class="button" data-e3f2-inspect="${esc(r.semantic_id)}">对照预览</button><button class="button" data-e3f2-edit="${esc(r.semantic_id)}">编辑节点</button></div></article>`).join(''):'<p class="e3f2-empty">这一组已整理完成。</p>';
    }
    async function analyze(){
      if(busy)return;busy=true;error='';report=null;const version=++generation,key=currentKey();paint();
      const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),8000);
      try{
        const response=await fetch('/api/role-expression/overview',{method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Studio':'e3f2'},signal:controller.signal,
          body:JSON.stringify({draft:draft(),source,context:source==='device'?connection():null,request_id:'overview-'+crypto.randomUUID()})});
        const value=await response.json();if(!response.ok)throw new Error(value.error||'角色核对未完成');
        if(version===generation&&key===currentKey()){report=value;acceptedKey=key;}else{error='角色或设备状态已变化，请重新核对。';}
      }catch(e){if(version===generation)error=e.name==='AbortError'?'核对超时，请重试。':e.message;}
      finally{clearTimeout(timeout);busy=false;paint();}
    }
    function navigate(semantic,edit){
      paint();if(!report)return;
      // Change the existing single-expression selection through its public UI;
      // playback remains an explicit action in that panel.
      const select=document.getElementById('e3e-semantic'),input=document.getElementById('e3e-source');
      if(!select||!input)return;input.value=source;select.value=semantic;select.dispatchEvent(new Event('change',{bubbles:true}));
      if(edit)document.querySelector('[data-e3e-edit]').click();
      else document.querySelector('.e3e-planner').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});
    }
    function init(){
      document.addEventListener('change',e=>{if(e.target.id==='e3f2-source'){source=e.target.value;generation++;report=null;error='';paint();}});
      document.addEventListener('click',e=>{
        const button=e.target.closest('button');if(!button)return;
        if(button.hasAttribute('data-e3f2-refresh'))void analyze();
        else if(button.dataset.e3f2Filter){filter=button.dataset.e3f2Filter;paint();}
        else if(button.dataset.e3f2Inspect)navigate(button.dataset.e3f2Inspect,false);
        else if(button.dataset.e3f2Edit)navigate(button.dataset.e3f2Edit,true);
      });
      paint();timer=setInterval(paint,400);window.addEventListener('pagehide',()=>clearInterval(timer));
    }
    return Object.freeze({view,init,state:()=>({source,filter,report:report?JSON.parse(JSON.stringify(report)):null,busy,error})});
  }
  window.DeskBotStudioE3F2UI=Object.freeze({create});
})();
