/* A separate polling island keeps unsaved shell settings and focus intact. */
(function(root){
  'use strict';
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const reasons={stopped:'尚未开始',finishing:'正在核对上次请求',config_updated:'配置已更新，重新开始采用新版',
    registration_required:'先完成采样验证',association_required:'先确认角色关联',await_connection:'等待对应桌宠连接',
    motion_unknown:'等待转动状态接入',moving:'转动中，等停稳后重新识别',service_error:'服务需要重新开始',
    session_required:'等待连接并开启会话',device_changed:'等待对应桌宠',read_roles:'读取桌宠角色',
    fresh_display_required:'等待最新画面状态',wait_ui:'等画面切换完成',speaking:'等这一句说完',updating:'等待资源更新完成',
    wait_home:'等回到主页',player_busy:'等待播放器空闲',owner_busy:'等当前请求完成',await_result:'等桌宠回报切换结果',
    ready_to_submit:'准备切换',wait_stable:'等待外壳停稳',stable_shell:'外壳已确认',keep_current:'保持当前角色',
    expression_visible:'新画面已显示',expression_saved:'角色已保存',content_required:'等待匹配的表情包',
    result_unknown:'正在查询上次结果',submission_unknown:'正在核对上次发送',retry_required:'查询完成，可以重试',
    save_failed:'画面已显示，保存需要重试',player_unavailable:'播放器稍后可重试',await_observation:'等待新采样',
    binding_required:'等待角色关联',content_ready:'角色内容已就绪',await_snapshot:'读取设备状态',
    same_role:'当前角色已匹配',already_current:'当前角色已匹配',not_registered:'等待登记',
    register_shell:'发现新外壳，可以添加登记',binding_updated:'角色关联有新版，等待确认',save_pending:'画面已显示，等待保存',
    disconnected:'等待连接',read_device_state:'读取桌宠当前角色',waiting_receipt:'等待设备回执',prepare_content:'准备角色内容',
    activate_expression:'正在发送新角色',observation_expired:'等待新的外壳识别',device_state_read:'已读取桌宠当前角色',
    sample_expired:'等新的磁场采样',await_stream:'等待磁场采样',await_new_samples:'等待停稳后的新采样'};
  const recognition={known:'已识别',unknown:'未知外壳',removed:'外壳已取下',unstable:'等待停稳',warming_up:'正在识别',ambiguous:'等待明确布局',missing:'等待采样',stale:'等待新采样'};
  function create(){
    let data=null,busy=false,message='',failed=false,project='',timer=null,requestNumber=0;
    const host=()=>document.getElementById('shell-runtime-root');
    async function request(action,value){
      const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
      try{
        const r=await fetch('/api/shell-runtime/'+action,{cache:'no-store',signal:controller.signal,...(value?{method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Studio':'shell-runtime-v1'},body:JSON.stringify(value)}:{})});
        const body=await r.json();if(!r.ok){const e=Error(body.error||'运行状态读取失败');e.state=body.state;throw e;}return body;
      }finally{clearTimeout(timeout);}
    }
    function paint(){
      const el=host();if(!el)return;
      if(!data){el.innerHTML=`<section class="sr-card"><h2>自动换角</h2><p role="status">${esc(message||'读取运行状态…')}</p><button data-sr-action="refresh">重新读取</button></section>`;return;}
      if(!data.projects.some(p=>p.session_id===project))project=data.pin?.session_id||data.projects.find(p=>p.ready)?.session_id||data.projects[0]?.session_id||'';
      const selected=data.projects.find(p=>p.session_id===project),runtime=data.runtime,adapter=runtime?.adapter,
        decision=adapter?.decision,attempt=adapter?.attempt,phase={running:'运行中',stopped:'已暂停',finishing:'请求收尾中',closed:'服务已关闭'}[data.phase],
        target=decision?.target?.role_name||'等待识别',actual=decision?.actual?.role_name||adapter?.device_expression?.actual||'等待读取',saved=decision?.saved?.role_name||adapter?.device_expression?.saved||'等待读取',
        readiness=!selected?.ready?(reasons[selected?.reason]||'先建立采样项目'):null,
        note=message||readiness||reasons[data.reason]||'等待下一次识别',
        steps=[['识别',recognition[runtime?.recognition?.status]||'等待采样'],['目标角色',target],['桌宠首帧',actual],['桌宠保存',saved]];
      // Identical polling results do not replace active controls or replay entrance animations.
      const html=`<section class="sr-card" aria-labelledby="sr-heading"><header class="sc-section-title"><div><span class="sc-eyebrow">AUTOMATIC CHARACTER</span><h2 id="sr-heading">换壳后，自动遇见它。</h2></div><span class="sc-chip" data-sr-phase="${esc(data.phase)}">${esc(phase)}</span></header><p>工作台服务持续运行，页面关闭后也会继续。每次重开服务，从这里开始。</p>
        <div class="sr-controls"><label>采样项目<select id="sr-project" ${busy||data.enabled||data.phase==='finishing'?'disabled':''}><option value="">选择已保存项目</option>${data.projects.map(p=>`<option value="${esc(p.session_id)}" ${p.session_id===project?'selected':''}>${esc(p.name)} · ${p.ready?'已就绪':esc(reasons[p.reason]||'待准备')}</option>`).join('')}</select></label><button class="sc-primary" data-sr-action="start" ${busy||data.enabled||!selected?.ready||!data.can_start?'disabled':''}>开始</button><button data-sr-action="stop" ${busy||!data.enabled?'disabled':''}>暂停</button><button data-sr-action="retry" ${busy||!data.can_retry?'disabled':''}>重试</button></div>
        <div class="sr-flow">${steps.map(([label,value],i)=>`<div><small>${label}</small><strong>${esc(value)}</strong></div>${i<steps.length-1?'<i>→</i>':''}`).join('')}</div>
        <div class="sr-note" role="status" data-error="${failed||!!data.error}"><span>${esc(note)}</span>${!selected?.ready?'<a href="#capture" data-navigate="capture">继续采样登记 ↗</a>':''}</div>
        ${data.error?`<p class="sr-error">${esc(data.error)}</p>`:''}${attempt?`<small class="sr-receipt">最近请求 · ${esc({submitted:'已发送',queued:'设备已接收',switching:'正在切换',visible:'首帧已显示',saved:'已保存',failed:'等待重试',uncertain:'正在查询',superseded:'旧请求已收尾'}[attempt.status]||attempt.status)} · ${esc(attempt.target.role_name)}</small>`:''}
        <footer>当前接入：表情角色切换 · 人格与声音下一阶段接入</footer></section>`;
      if(el._srHTML!==html){
        const focus=el.contains(document.activeElement)?document.activeElement.id:null;
        el.innerHTML=html;el._srHTML=html;if(focus)el.querySelector('#'+focus)?.focus();
      }
    }
    function schedule(){clearTimeout(timer);timer=setTimeout(()=>{if(host())sync();},1000);}
    async function sync(){
      if(busy)return;const number=++requestNumber;
      try{const value=await request('state');if(number!==requestNumber)return;data=value;failed=false;message='';}
      catch(e){if(number!==requestNumber)return;failed=true;message=e.name==='AbortError'?'读取超时，稍后重试':e.message;}
      finally{if(number===requestNumber){paint();schedule();}}
    }
    async function action(kind){
      if(kind==='refresh'){message='';return sync();}if(busy||!data)return;
      busy=true;++requestNumber;clearTimeout(timer);message='正在处理…';failed=false;paint();
      try{data=await request(kind,{expected_generation:data.generation,...(kind==='start'?{session_id:project}:{})});message=kind==='stop'?(data.phase==='finishing'?'已暂停新切换，等待上次请求完成':'自动换角已暂停'):kind==='retry'?'正在核对并重试':'自动换角已开始';}
      catch(e){if(e.state)data=e.state;failed=true;message=e.name==='AbortError'?'操作结果待确认，正在重新读取':e.message;}
      finally{busy=false;paint();schedule();}
    }
    function init(){
      document.addEventListener('click',e=>{const button=e.target.closest('[data-sr-action]');if(button&&!button.disabled)action(button.dataset.srAction);});
      document.addEventListener('change',e=>{if(e.target.id==='sr-project'){project=e.target.value;message='';paint();}});
    }
    return Object.freeze({init,mount:()=>{paint();if(host())sync();},paint,state:()=>structuredClone({data,busy,message,failed,project})});
  }
  root.DeskBotShellRuntime=Object.freeze({create});
})(globalThis);
