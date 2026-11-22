/* Everyday shell choices; capture details remain in the advanced tool. All rehearsal is offline. */
(function(root){
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const recognition={not_registered:'识别待登记',collecting:'识别采样中',needs_review:'识别待复核',source_updated:'登记有新版',historical_only:'历史演练来源',validated_offline:'离线识别已验证'};
  const reasons={wait_stable:'等外壳停稳',stable_shell:'目标已确认',activate_expression:'发送模拟切换',expression_visible:'模拟首帧已显示',expression_saved:'模拟选择已保存',already_current:'保持当前角色',keep_current:'保留当前画面',speaking:'等这句话说完',moving:'等转动结束',updating:'等内容准备完成',prepare_content:'准备表情内容',content_ready:'内容已就绪',waiting_receipt:'等待模拟回执',save_pending:'等待保存回执',register_shell:'提示添加新外壳',disconnected:'连接已断开',reobserve:'重新读取设备状态',device_state_read:'已读取模拟设备',read_device_state:'等设备状态更新',old_receipt:'丢弃迟到回执',first_frame_required:'等首帧确认',binding_updated:'关联版本待确认',binding_required:'等待选择角色',observation_expired:'识别已过期，等新观察',idle:'当前一句结束'};
  reasons.old_snapshot='忽略旧连接状态';
  Object.assign(reasons,{session_required:'等待建立会话',read_roles:'读取角色目录',fresh_display_required:'等待最新画面状态',wait_ui:'等页面切换完成',wait_home:'等回到主页',player_busy:'等播放器空闲',owner_busy:'等当前请求结束',queued:'设备已接收',switching:'正在切换',result_unknown:'查询上次执行结果',submission_unknown:'核对命令交接结果',retry_required:'查询完成，等待重试',save_failed:'画面已生效，保存待重试',player_unavailable:'设备暂未接收，稍后重试',old_observation:'忽略重复或旧识别',old_connection:'忽略旧连接',old_state:'忽略旧设备状态',previous_binding_applied:'旧请求已生效，重新核对角色版本',association_required:'等待有效登记关联',content_required:'等待匹配的角色内容',device_changed:'等待对应桌宠'});
  function create(){
    const runtime=DeskBotShellRuntime.create();
    const installation=DeskBotShellInstallation.create();
    let data=null,form=null,dirty=false,busy=false,message='',error=false,selection='',scenario='speaking',sessionScenario='speaking',a='',b='',project='',bChosen=false;
    const visible=()=>!!document.getElementById('shell-characters-root');
    const make=()=>({shell_id:'shell-'+crypto.randomUUID().replaceAll('-',''),name:'',role_id:'',profile_revision:0,source_key:'',source_sha256:null});
    const current=()=>data?.shells.find(s=>s.shell_id===selection);
    async function request(action,value){
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),action==='replay-session'?60000:15000);
      try{
        const r=await fetch('/api/shell-characters/'+action,{cache:'no-store',signal:controller.signal,...(value?{method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Studio':'shell-characters-v1'},body:JSON.stringify(value)}:{})});
        const body=await r.json();if(!r.ok){const e=Error(body.error||'外壳请求暂未完成');e.state=body.state;throw e;}return body;
      }finally{clearTimeout(timer);}
    }
    function open(id){
      if(busy||dirty&&!confirm('这次选择还未保存。放弃修改并打开另一个外壳？'))return;
      selection=id;const s=data?.shells.find(s=>s.shell_id===id),role=data?.characters.find(p=>p.role_id===s?.binding?.role_id);
      form=s?{shell_id:s.shell_id,name:s.name,role_id:role?.role_id||'',profile_revision:role?.profile_revision||0,source_key:s.binding?.source?.key||'',source_sha256:data.sources.find(r=>r.key===s.binding?.source?.key)?.archive_sha256??null}:make();
      dirty=false;message='';error=false;paint();
    }
    function choices(){
      const confirmed=data?.shells.filter(s=>s.binding&&s.role_current)||[];
      if(!confirmed.some(s=>s.shell_id===a))a=confirmed[0]?.shell_id||'';
      if(!confirmed.some(s=>s.shell_id===b)){b=confirmed.find(s=>s.shell_id!==a)?.shell_id||a;bChosen=false;}
      if(!bChosen&&a===b&&confirmed.length>1)b=confirmed.find(s=>s.shell_id!==a).shell_id;
    }
    async function sync(){
      if(busy)return;busy=true;status();
      try{data=await request('state');error=false;
        if(!form){selection=data.shells[0]?.shell_id||'';openAfterRead();}
        else if(!dirty){openAfterRead();message='已读取最新配置';}
        else{
          const committed=data.shells.find(s=>s.shell_id===form.shell_id);
          if(committed&&committed.name===form.name&&committed.binding?.role_id===form.role_id&&committed.binding?.profile_revision===form.profile_revision&&
              (committed.binding?.source?.key||'')===form.source_key&&(committed.binding?.source?.archive_sha256??null)===form.source_sha256){
            selection=form.shell_id;dirty=false;message='已核对保存结果，关联已保存在电脑';
          }else message='最新配置已读取，当前选择保留；可载入已保存关联';
        }
        choices();
      }catch(e){message=e.name==='AbortError'?'读取超时，请重试':e.message;error=true;}
      finally{busy=false;paint();}
    }
    function openAfterRead(){
      const s=current(),role=data.characters.find(p=>p.role_id===s?.binding?.role_id);
      if(s)form={shell_id:s.shell_id,name:s.name,role_id:role?.role_id||'',profile_revision:role?.profile_revision||0,source_key:s.binding?.source?.key||'',source_sha256:data.sources.find(r=>r.key===s.binding?.source?.key)?.archive_sha256??null};
      else if(!form)form=make();
    }
    async function save(){
      if(busy||!data||!form?.role_id)return;
      busy=true;error=false;message='正在保存…';status();
      try{
        const old=data.shells.find(s=>s.shell_id===form.shell_id);
        if(!old||old.name!==form.name){
          data=await request('save-shell',{shell_id:form.shell_id,name:form.name,expected_revision:data.revision});selection=form.shell_id;
        }
        data=await request('confirm',{shell_id:form.shell_id,role_id:form.role_id,profile_revision:form.profile_revision,
          source_key:form.source_key||null,source_sha256:form.source_sha256,expected_revision:data.revision});
        selection=form.shell_id;dirty=false;message='关联已保存在电脑';choices();
      }catch(e){if(e.state)data=e.state;error=true;message=e.name==='AbortError'?'保存结果待确认，请重新读取；当前选择已保留':e.message;}
      finally{busy=false;paint();}
    }
    async function run(mode='simulate'){
      const logs=mode==='replay'||mode==='replay-session',lab=mode==='session-lab';
      if(busy||!data)return;busy=true;error=false;message=logs?'正在重读已有日志…':lab?'正在联测主机会话与内存设备…':'正在演练换角…';status();
      try{data=await request(mode,logs?{expected_revision:data.revision,session_id:project}:{expected_revision:data.revision,scenario:lab?sessionScenario:scenario,shell_a:a,shell_b:b});message=logs?'日志回放完成':lab?'内存设备会话联测完成':'离线演练完成';}
      catch(e){if(e.state)data=e.state;error=true;message=e.name==='AbortError'?'联测结果待确认，请重新读取':e.message;}
      finally{busy=false;paint();}
    }
    function cards(){
      return `<aside class="sc-shells"><div class="sc-section-title"><strong>我的外壳</strong><span>${data?.shells.length||0}</span></div>${(data?.shells||[]).map(s=>`<button type="button" data-sc-open="${esc(s.shell_id)}" aria-pressed="${s.shell_id===selection}"><span class="sc-shell-mark">◒</span><span><strong>${esc(s.name)}</strong><small>${esc(s.role_name||'待选择角色')}</small><small>${esc(recognition[s.recognition]||s.recognition)}</small></span></button>`).join('')}<button type="button" class="sc-add" data-sc-action="add">＋ 添加外壳</button></aside>`;
    }
    function formView(){
      if(!data||!form)return '<p>正在读取外壳配置…</p>';
      const shell=current(),role=data.characters.find(p=>p.role_id===form.role_id);
      return `<section class="sc-editor"><div class="sc-section-title"><div><span class="sc-eyebrow">${shell?'SHELL SETTINGS':'NEW SHELL'}</span><h2>${shell?'外壳与角色':'添加一个外壳'}</h2></div><span class="sc-chip" id="sc-draft">${dirty?'编辑中':shell?.binding?'电脑已保存':'新的外壳'}</span></div>
        <div class="sc-guide"><span><b>1</b> 起个名字</span><i>→</i><span><b>2</b> 选角色</span><i>→</i><span><b>3</b> 确认关联</span></div>
        <form id="sc-form"><label>外壳名字<input name="shell_name" maxlength="40" required value="${esc(form.name)}" placeholder="例如：白色圆团"></label>
        <label>对应角色<select name="character_id" required><option value="">选择一个完整角色</option>${data.characters.map(p=>`<option value="${esc(p.role_id)}" ${p.role_id===form.role_id?'selected':''} ${!p.current?'disabled':''}>${esc(p.display_name)} · 修订 ${p.profile_revision}${!p.current?' · 表情待更新':''}</option>`).join('')}</select></label>
        <p class="sc-role-summary" id="sc-role-summary">${esc(role?'已保存角色 · '+role.display_name:'先在“我的角色”保存名字、人格和表情形象。')}</p><a href="#characters" data-navigate="characters">去我的角色预览 ↗</a>
        <label>登记来源<select name="registration_source"><option value="">稍后采样登记</option>${data.sources.map(s=>`<option value="${esc(s.key)}" ${s.key===form.source_key?'selected':''}>${esc(s.name)} · ${esc(s.project_name)} · ${esc(recognition[s.recognition])}</option>`).join('')}</select><small>已有采样可以直接关联，新的外壳可先保存角色选择。</small></label>
        ${shell?.binding&&!shell.role_current?'<p class="sc-update">角色已有新版，确认后关联到当前修订。</p>':''}
        <button class="sc-primary" type="submit" ${!role||busy?'disabled':''}>确认关联</button></form>
        ${shell?.binding?`<div class="sc-status-grid"><div><span>角色选择</span><strong>${shell.role_current?'已确认':'有新版待确认'}</strong></div><div><span>识别验证</span><strong>${esc(recognition[shell.recognition])}</strong></div><div><span>设备同步</span><strong>后续实机接入</strong></div></div>`:''}
      </section>`;
    }
    function rehearsals(){
      if(!data)return '';
      const shells=data.shells.filter(s=>s.binding&&s.role_current),options=selected=>shells.map(s=>`<option value="${esc(s.shell_id)}" ${s.shell_id===selected?'selected':''}>${esc(s.name)} · ${esc(s.role_name)}</option>`).join('');
      const sources=[...new Map(data.sources.filter(s=>s.archive_sha256).map(s=>[s.session_id,s])).values()];if(!sources.some(s=>s.session_id===project))project=sources[0]?.session_id||'';
      return `<section class="sc-rehearsal"><header class="sc-section-title"><div><span class="sc-eyebrow">OFFLINE REHEARSAL</span><h2>换一件外壳，会发生什么？</h2></div><span class="sc-chip">电脑离线演练</span></header><p>用模拟信号检查换角时机，画面状态与真实桌宠分别显示。</p>
        <div class="sc-rehearse-controls"><label>当前外壳<select id="sc-a">${options(a)}</select></label><span>→</span><label>换上的外壳<select id="sc-b">${options(b)}</select></label><label>场景<select id="sc-scenario">${Object.entries(data.scenarios).map(([id,name])=>`<option value="${id}" ${id===scenario?'selected':''}>${esc(name)}</option>`).join('')}</select></label><button type="button" class="sc-primary" data-sc-action="simulate" ${!shells.length||busy?'disabled':''}>演练换角</button></div>
        <details class="sc-session-lab"><summary>会话联测 · 检查发送、首帧和保存</summary><p>沿用上方两件外壳，用真实主机协议连接内存模拟桌宠，查看失败、重连与重试过程。</p><div><label>联测场景<select id="sc-session-scenario">${Object.entries(data.session_scenarios||{}).map(([id,name])=>`<option value="${id}" ${id===sessionScenario?'selected':''}>${esc(name)}</option>`).join('')}</select></label><button type="button" class="sc-primary" data-sc-action="session-lab" ${!shells.length||busy?'disabled':''}>会话联测</button></div></details>
        ${!shells.length?'<div class="sc-empty">先保存一个外壳的角色关联，即可开始；添加两种外壳可以比较切换效果。</div>':''}
        ${resultView()}
        <details class="sc-replay"><summary>用已有原日志检查角色解析</summary><p>每个保存窗口分别重放，查看识别结果；“原日志会话联测”继续核对模拟设备的首帧和保存。</p><select id="sc-project"><option value="">选择已评估项目</option>${sources.map(s=>`<option value="${esc(s.session_id)}" ${s.session_id===project?'selected':''}>${esc(s.project_name)}</option>`).join('')}</select><button type="button" data-sc-action="replay" ${!project||busy?'disabled':''}>重放已有日志</button><button type="button" data-sc-action="replay-session" ${!project||busy?'disabled':''}>原日志会话联测</button></details></section>`;
    }
    function resultView(){
      const run=data.last_run;if(!run)return '';
      const warning=run.current?'':'<p class="sc-update">配置有更新，重新演练即可查看最新结果。</p>';
      if(run.kind==='runtime_replay')return `<div class="sc-result sc-runtime-result">${warning}<h3>原日志 → 识别器 → 内存设备</h3><p>${run.rows.length} 个独立窗口 · ${run.total_samples} 条原采样 · ${run.simulated_activation_count} 次模拟切换。每个窗口分别启动模拟会话。</p><div class="sc-log-rows">${run.rows.map(r=>`<div><span>${esc(r.recording_id)}</span><span>${esc(r.shell_name||r.profile_label||'未确认外壳')}</span><strong>${esc(r.error?'执行待重试':r.state.saved?.role_name?'模拟保存 · '+r.state.saved.role_name:r.input_final_profile?r.role_name?'登记验证待完成':'识别成功，待关联角色':'保持当前画面')}</strong></div>`).join('')}</div>${run.rows.filter(r=>r.simulated_activation_count).map(r=>`<details class="sc-runtime-trace"><summary>${esc(r.shell_name||r.profile_label||r.recording_id)} · 发送与回执</summary><ol class="sc-timeline">${r.timeline.filter(v=>['request','first_frame','saved','failed'].includes(v.event)).map(v=>`<li><time>${(v.at_ms/1000).toFixed(2)}s</time><span>${esc(reasons[v.reason]||v.reason)}</span><strong>${esc(v.role_name||'')}</strong></li>`).join('')}</ol></details>`).join('')}<p>登记与关联状态见上方外壳设置；人格与声音后续接入。</p></div>`;
      if(run.kind==='log_windows')return `<div class="sc-result">${warning}<h3>原日志 · 逐窗口解析</h3><div class="sc-log-rows">${run.rows.map(r=>`<div><span>${esc(r.recording_id)}</span><span>${esc(r.shell_name||'未确认外壳')}</span><strong>${esc(r.role_id?r.role_name:'保留当前角色')}</strong></div>`).join('')}</div><p>这些是独立采样窗口的解析结果，连续换角时机见场景演练。</p></div>`;
      const s=run.state,label=v=>v?.role_name||v?.expression_role_id||'保持原画面';
      const lab=run.kind==='session_lab';
      return `<div class="sc-result">${warning}${lab?`<div class="sc-lab-result"><strong>内存设备 · 会话联测</strong><span>${esc(run.label)} · ${run.simulated_activation_count} 次模拟切换</span><small>识别与关联 → 发送请求 → 首帧 → 保存 · 人格与声音待接入</small></div>`:''}<div class="sc-result-flow"><div><small>演练目标</small><strong>${esc(label(s.target))}</strong></div><i>→</i><div><small>模拟首帧</small><strong>${esc(label(s.actual))}</strong></div><i>→</i><div><small>模拟保存</small><strong>${esc(label(s.saved))}</strong></div></div>
        <ol class="sc-timeline">${run.timeline.filter((row,i,rows)=>i===0||row.reason!==rows[i-1].reason).map((row,i)=>`<li style="--sc-i:${i}"><time>${(row.at_ms/1000).toFixed(2)}s</time><span>${esc(reasons[row.reason]||row.reason)}</span>${row.role_name||row.target?.role_name||row.actual?.role_name||row.saved?.role_name||row.request?.target?.role_name?`<strong>${esc(row.role_name||row.target?.role_name||row.actual?.role_name||row.saved?.role_name||row.request?.target?.role_name)}</strong>`:''}</li>`).join('')}</ol></div>`;
    }
    function view(){return '<main id="shell-characters-root" class="sc-root"></main>';}
    function paint(){
      const el=document.getElementById('shell-characters-root');if(!el)return;
      el.innerHTML=`<header class="sc-heading"><div><span class="sc-eyebrow">SHELLS & CHARACTERS</span><h1>换个外壳，遇见它。</h1><p>每个外壳选择一个角色，表达、性格和声音沿用同一份档案。</p></div><a href="#capture" data-navigate="capture">采样与日志工具 ↗</a></header>
      <div class="sc-feedback" role="status"><p id="sc-message"></p><button type="button" data-sc-action="refresh">重新读取配置</button>${selection?'<button type="button" data-sc-action="load">载入已保存关联</button>':''}</div>
      <div id="shell-installation-root"></div><div class="sc-workspace">${cards()}${formView()}</div><div id="shell-runtime-root"></div>${rehearsals()}`;status();runtime.mount();installation.mount();
    }
    function status(){
      const el=document.getElementById('shell-characters-root');if(!el)return;
      const msg=el.querySelector('#sc-message');if(msg){msg.textContent=message;msg.dataset.error=String(error);}
      el.querySelectorAll('input,select,button').forEach(e=>{if(!e.closest('#shell-runtime-root,#shell-installation-root'))e.disabled=busy;});
      const submit=el.querySelector('#sc-form [type="submit"]');if(submit)submit.disabled=busy||!data?.characters.some(p=>p.role_id===form?.role_id&&p.current);
      const sim=el.querySelector('[data-sc-action="simulate"]');if(sim)sim.disabled=busy||!a||!b;
      const lab=el.querySelector('[data-sc-action="session-lab"]');if(lab)lab.disabled=busy||!a||!b;
      const replay=el.querySelector('[data-sc-action="replay"]');if(replay)replay.disabled=busy||!project;
      const replaySession=el.querySelector('[data-sc-action="replay-session"]');if(replaySession)replaySession.disabled=busy||!project;
      const note=el.querySelector('#sc-draft');if(note)note.textContent=busy?'正在处理…':dirty?'选择未保存':current()?.binding?'电脑已保存':'新的外壳';
      const role=el.querySelector('#sc-role-summary');if(role)role.textContent=data?.characters.find(p=>p.role_id===form?.role_id)?.display_name||'先在“我的角色”保存名字、人格和表情形象。';
    }
    function init(){
      runtime.init();
      installation.init();
      document.addEventListener('input',e=>{if(e.target.closest('#sc-form')&&!busy){
        if(e.target.name==='shell_name')form.name=e.target.value;
        if(e.target.name==='character_id'){form.role_id=e.target.value;form.profile_revision=data.characters.find(p=>p.role_id===form.role_id)?.profile_revision||0;}
        if(e.target.name==='registration_source'){form.source_key=e.target.value;form.source_sha256=data.sources.find(s=>s.key===form.source_key)?.archive_sha256??null;}
        dirty=true;message='';error=false;status();
      }});
      document.addEventListener('change',e=>{if(e.target.id==='sc-a')a=e.target.value;if(e.target.id==='sc-b'){b=e.target.value;bChosen=true;}if(e.target.id==='sc-scenario')scenario=e.target.value;if(e.target.id==='sc-session-scenario')sessionScenario=e.target.value;if(e.target.id==='sc-project'){project=e.target.value;status();}});
      document.addEventListener('submit',e=>{if(e.target.id==='sc-form'){e.preventDefault();save();}});
      document.addEventListener('click',e=>{
        const button=e.target.closest('[data-sc-open],[data-sc-action]');if(!button||button.disabled||busy)return;
        if(button.hasAttribute('data-sc-open'))return open(button.dataset.scOpen);
        switch(button.dataset.scAction){case 'add':open('');break;case 'load':open(selection);break;case 'refresh':sync();break;case 'simulate':run();break;case 'session-lab':run('session-lab');break;case 'replay':run('replay');break;case 'replay-session':run('replay-session');break;}
      });
      window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
      sync();
    }
    return Object.freeze({view,mount:()=>{if(visible()){paint();sync();}},init,runtime:()=>runtime.state(),state:()=>JSON.parse(JSON.stringify({data,form,dirty,busy,message,error,selection,scenario,sessionScenario,a,b,project}))});
  }
  root.DeskBotShellCharacters=Object.freeze({create});
})(globalThis);
