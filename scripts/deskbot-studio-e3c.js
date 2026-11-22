(function(){
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const states={idle:'待机',starting:'启动中',wifi_configuring:'配置网络',connecting:'连接中',listening:'聆听',speaking:'说话中',upgrading:'升级中',activating:'激活中',audio_testing:'音频测试',fatal_error:'故障',home:'主页',other:'其他页面',initialized:'已就绪',unavailable:'未就绪',stopping:'停止中',unknown:'等待查询'};
  const reasons={enable_to_query:'启用后读取应用、页面与播放器状态',opening_session:'正在建立设备会话',querying_display:'正在读取显示状态',sleep_unknown:'主页待机，可以提交表情请求',session_reply_timeout:'当前固件尚未响应状态接口；设备联调时接入 E3c 固件',application_busy:'正在对话或处理任务，回到待机后可提交',application_transitioning:'应用正在切换状态',page_transitioning:'页面正在切换',not_home:'回到桌宠主页后可提交',player_unknown:'等待播放器状态',player_unavailable:'播放器尚未就绪',player_stopping:'播放器正在停止',application_unknown:'等待应用状态',page_unknown:'等待页面状态',state_stale:'状态已过期，等待更新',connection_lost:'USB 连接已断开',identity_changed:'设备已重启，请重新启用状态查询',identity_unavailable:'等待设备更新身份',query_stopped:'状态查询已停止',session_changed:'设备会话已变化，请重新启用',lease_expired:'会话已到期，请重新启用',session_closed:'设备会话已关闭',display_unavailable:'当前固件未提供显示查询',request_not_ready:'等待主页待机与最新状态',already_enabled:'状态查询已经启用',queued_for_owner:'等待应用主循环处理',submitted_to_display:'已调用原播放器入口',device_receipt_timeout:'等待设备回执超时，可先查看当前状态',deadline_before_dispatch:'排队期间请求到期',catalog_or_role_changed:'设备目录或角色已变化',unsupported_command:'当前设备尚未支持此请求',pending_request:'上一条请求仍在处理',callback_reserved:'等待上一项调度完成',replayed_receipt:'设备返回已有处理结果',command_processed:'连接进程已处理请求'};
  const statuses={accepted:'已受理',dispatched:'已提交播放器',busy:'设备忙碌',rejected:'设备拒绝',interrupted:'请求已取消',expired:'请求已过期',duplicate:'已有处理结果'};
  Object.assign(reasons,{connection_changed:'USB 连接已更新，请等待状态刷新',target_mismatch:'目标设备已变化',boot_changed:'设备已重启，请重新启用',request_id_conflict:'请求编号与内容不一致',sequence_not_increasing:'请求顺序已变化，请重新启用',deadline_out_of_range:'请求有效期超出范围',expression_interface_pending:'当前固件尚未接入这个表情请求',unsupported_expression_or_action:'当前固件尚未支持这个表情或动作',existing_owner_or_request:'原应用或上一条请求正在处理',ticket_counter_exhausted:'本次会话请求已满，请重新启用',clock_out_of_range:'设备计时已变化，请重新启用',session_changed_before_dispatch:'执行前设备会话已变化',owner_changed_before_dispatch:'执行前应用状态已变化',session_unavailable:'设备会话暂不可用',unavailable:'等待设备接口更新'});
  function create(host){
    const readiness=window.DeskBotStudioPlayReadiness;
    let value=null,error='',busy=false,polling=false,timer=0,historyKey='',generation=0;
    let lastDisplay=null,directoryAttempt=null,preparing=false,closed=false,actionError='';
    const local=()=>location.protocol==='http:'&&location.hostname==='127.0.0.1';
    const text=(id,s)=>{const e=document.getElementById(id);if(e&&e.textContent!==String(s))e.textContent=String(s);};
    function view(){return `<section class="surface e3c-control" aria-label="设备实时状态与表情请求"><div class="e3c-heading"><div><div class="eyebrow">LIVE EXPRESSION</div><h2>让表达走到桌宠。</h2><p>查看身体状态，选择表情并核对回执。</p></div><span id="e3c-link-status" class="e3c-badge" role="status">等待 USB</span></div><div class="e3c-path" aria-label="请求链路"><span>电脑工作台</span><i></i><span>USB 会话</span><i></i><span>应用主循环</span><i></i><span>原表情播放器</span></div><div class="e3c-facts"><div><span>应用</span><strong id="e3c-app">等待查询</strong></div><div><span>页面</span><strong id="e3c-page">等待查询</strong></div><div><span>播放器</span><strong id="e3c-player">等待查询</strong></div></div><div class="e3c-command"><div><b>平静 · neutral</b><span>当前绑定 winking.eaf</span></div><button class="button button-primary" data-e3c-action="neutral" disabled>播放平静</button><button class="button" data-e3c-action="enable" disabled>启用状态查询</button></div><div class="e3c-command"><div><b>害羞 · embarrassed</b><span id="e3c-shy-binding">读取目录后显示当前绑定</span><small>沿用原有蹭蹭动作</small></div><button class="button button-primary" data-e3c-action="embarrassed" disabled>播放害羞</button></div><div class="e3c-command"><div><b>进食 · eat</b><span id="e3c-eat-binding">读取目录后显示当前绑定</span><small>单次播放 · 无附带动作</small></div><button class="button button-primary" data-e3c-action="eat" disabled>播放进食</button></div><p id="e3c-reason" class="e3c-reason">连接 USB 后，启用设备状态查询。</p><div class="e3c-result" role="status"><span class="e3c-result-dot"></span><div><strong id="e3c-outcome">等待你的第一次请求</strong><p id="e3c-outcome-note">设备回执会在这里更新。</p></div><small id="e3c-age">来源：USB 设备查询</small></div><details class="e3c-history"><summary>设备回执 <b id="e3c-receipt-count">0</b></summary><ol id="e3c-receipts"><li>尚无设备回执</li></ol></details><p class="e3c-footnote">“已提交播放器”表示已调用播放入口。睡眠状态当前为未知，圆屏效果由实机确认。</p></section>`;}
    async function api(path,body,budget=4000){
      const abort=new AbortController(),limit=setTimeout(()=>abort.abort(),Math.max(1,budget));
      try{const r=await fetch(path,{...(body?{method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Studio':'e3c6'},body:JSON.stringify(body)}:{cache:'no-store'}),signal:abort.signal});const d=await r.json();if(!r.ok)throw new Error(d.error||'本机服务暂不可用');return d;}finally{clearTimeout(limit);}
    }
    function activeKey(v=value){
      const body=host.observation().device,hello=body?.identity?.status==='confirmed'?body.identity.hello:null;
      return body?.connection?.status==='connected'&&hello&&v?.target_id===hello.device_id&&v?.boot_id===hello.boot_id?readiness.sessionKey(v):null;
    }
    function accept(next){
      const previousKey=readiness.sessionKey(value);
      value=next;
      const key=activeKey(next);
      if(!key||key!==previousKey){lastDisplay=null;actionError='';}
      if(key&&next.display)lastDisplay={key,display:next.display,age:next.age_ms,at:performance.now()};
    }
    function paint(){
      const slot=document.getElementById('studio-control-slot');if(!slot)return;
      const body=host.observation().device,hello=body?.identity?.status==='confirmed'?body.identity.hello:null;
      const connected=body?.connection?.status==='connected',v=value,identityMatch=Boolean(hello&&v?.target_id===hello.device_id&&v?.boot_id===hello.boot_id);
      const key=!error?activeKey():null,current=key?v?.display:null,d=current||(key&&lastDisplay?.key===key?lastDisplay.display:null);
      text('e3c-link-status',error?'连接待恢复':v?.enabled?'会话已启用':connected?'USB 已连接':'等待 USB');
      slot.querySelector('.e3c-control')?.classList.toggle('is-live',Boolean(d));
      for(const name of ['application','page']){const c=d?.[name];text(name==='application'?'e3c-app':'e3c-page',c?.phase==='transitioning'?'切换中':c?.phase==='settled'?(states[c.value]||c.value):'等待查询');}
      text('e3c-player',states[d?.player?.state]||'等待查询');
      const commandWaiting=Boolean(v?.host_command);
      const play=slot.querySelector('[data-e3c-action="neutral"]'),enable=slot.querySelector('[data-e3c-action="enable"],[data-e3c-action="disable"]');
      const occupied=busy||commandWaiting||Boolean(v?.pending),usable=Boolean(key&&d&&readiness.available(d)&&!['reading'].includes(v?.catalog?.status));
      if(play){play.disabled=occupied||!usable;play.textContent=preparing?'核对后播放…':occupied?'请求处理中…':'播放平静';}
      const semantics=d?.expression_semantics??['neutral'];
      if(play&&!semantics.includes('neutral'))play.disabled=true;
      const directory=v?.catalog,shy=slot.querySelector('[data-e3c-action="embarrassed"]');
      const sameDirectory=Boolean(key&&directory?.status==='complete'&&directory.manifest&&['target_id','boot_id','session_id'].every(k=>directory[k]===v[k]));
      const binding=sameDirectory?directory.manifest.entries.find(r=>r.kind==='expression'&&r.id==='embarrassed'):null;
      text('e3c-shy-binding',binding?`目录绑定 ${binding.resource_id} · ${binding.fps} fps`:'读取当前设备目录后显示绑定');
      if(shy){shy.disabled=occupied||!usable||!binding||!semantics.includes('embarrassed');shy.title=!d?'启用状态查询后可核对':!semantics.includes('embarrassed')?'接入害羞固件后可播放':!binding?'正在准备设备目录':'点击后核对最新状态，再播放害羞';}
      const eat=slot.querySelector('[data-e3c-action="eat"]');
      const eating=sameDirectory?directory.manifest.entries.find(r=>r.kind==='expression'&&r.id==='eat'):null;
      text('e3c-eat-binding',eating?`目录绑定 ${eating.resource_id} · ${eating.fps} fps · ${eating.loop?'循环':'单次'}`:'读取当前设备目录后显示绑定');
      if(eat){eat.disabled=occupied||!usable||!eating||!semantics.includes('eat');eat.textContent=preparing?'核对后播放…':'播放进食';eat.title=!d?'启用状态查询后可核对':!semantics.includes('eat')?'安装进食版本后可播放':!eating?'正在准备设备目录':'点击后核对最新状态，再播放进食';}
      let cancel=slot.querySelector('[data-e3c-action="cancel"]');
      if(!cancel){cancel=document.createElement('button');cancel.className='button';cancel.dataset.e3cAction='cancel';cancel.textContent='取消本次播放';cancel.title='停止等待，不发送表情请求';eat?.after(cancel);}
      cancel.style.display=preparing?'inline-flex':'none';
      cancel.disabled=!preparing;
      if(enable){enable.dataset.e3cAction=v?.enabled?'disable':'enable';enable.textContent=v?.enabled?'停止状态查询':'启用状态查询';enable.disabled=busy||commandWaiting||!local()||Boolean(error)||!connected||!hello||!v?.connection_id;}
      const directoryNote=v?.host_command?.operation==='read_catalog'?'正在准备表情目录…':directory?.status==='reading'?`正在准备表情目录 · ${directory.received_entries||0}${directory.total?' / '+directory.total:''}`:null;
      text('e3c-reason',error||actionError||(preparing?'正在核对最新状态，就绪后播放一次。':null)||directoryNote||(v?.host_command?.status==='timeout'?'连接进程响应超时，请停止 USB 观察后重新连接。':null)||(v?.last_command?.status==='rejected'?(reasons[v.last_command.reason]||v.last_command.reason):d&&!current?'USB 已连接，正在刷新状态；可点击表情，核对后播放。':reasons[v?.reason])||'连接 USB 后，启用设备状态查询。');
      const currentReceipts=(v?.receipts||[]).filter(r=>r.target_id===v.target_id&&r.boot_id===v.boot_id&&r.session_id===v.session_id),last=currentReceipts.at(-1);
      const result=v?.local_result;
      let outcome='等待你的第一次请求',note='设备回执会在这里更新。';
      if(error){outcome='连接待恢复';note='恢复连接后读取状态与设备回执。';}
      else if(preparing){outcome='正在核对设备';note='最新状态就绪后，播放一次所选表情。';}
      else if(busy){outcome='正在发送';note='请求已交给本机连接进程。';}
      else if(actionError){outcome='本次播放已结束';note=actionError;}
      else if(result){outcome=result.outcome==='timeout'?'等待回执超时':result.outcome==='connection_lost'?'连接已断开':'请求已取消';note=reasons[result.reason]||result.reason;}
      else if(v?.pending){outcome=v.pending.phase==='accepted'?'已受理':'已发送，等待设备';note=v.pending.phase==='accepted'?'等待应用主循环提交给播放器。':'正在等待设备返回处理结果。';}
      else if(last){outcome=statuses[last.status]||last.status;note=reasons[last.reason]||last.reason;}
      else if(commandWaiting&&['neutral','embarrassed','eat'].includes(v.host_command.operation)){outcome='正在发送';note='请求已交给本机连接进程。';}
      text('e3c-outcome',outcome);text('e3c-outcome-note',note);
      const shownAge=current?v.age_ms:lastDisplay?lastDisplay.age+performance.now()-lastDisplay.at:0;
      text('e3c-age',current?`设备查询 · ${(v.age_ms/1000).toFixed(1)} 秒前`:d?`上次查询 · 正在刷新 · ${(shownAge/1000).toFixed(1)} 秒前`:'来源：USB 设备查询');
      text('e3c-receipt-count',(v?.receipts||[]).length);
      const receiptKey=JSON.stringify(v?.receipts||[]),list=document.getElementById('e3c-receipts');
      if(list&&(receiptKey!==historyKey||!list.dataset.ready)){list.innerHTML=(v?.receipts||[]).slice(-6).reverse().map(r=>`<li><span>#${esc(r.sequence)}</span><strong>${esc(statuses[r.status]||r.status)}</strong><small>${esc(reasons[r.reason]||r.reason)} · ${r.boot_id===v.boot_id&&r.session_id===v.session_id?'当前会话':'上一会话'}</small></li>`).join('')||'<li>尚无设备回执</li>';historyKey=receiptKey;list.dataset.ready='true';}
    }
    async function refresh(){
      if(!local()){error='设备连接请打开本机工作台。';paint();return;}
      if(polling||busy)return;polling=true;const version=generation;
      try{const next=await api('/api/studio/state');if(version===generation){accept(next);error='';}}
      catch(e){if(version===generation){error=e.name==='AbortError'?'本机服务响应超时':e.message;value=null;lastDisplay=null;}}
      finally{polling=false;paint();void prepareDirectory();}
    }
    async function prepareDirectory(){
      const key=activeKey();
      if(!host.automaticDirectory||closed||busy||error||!key||value.host_command||value.pending||value.catalog?.status!=='idle'||directoryAttempt===key)return;
      // Read-only once per session; failed reads remain available in the directory panel.
      directoryAttempt=key;
      await command('read_catalog');
    }
    async function command(operation){
      if(operation==='cancel'){if(preparing){generation++;actionError='已取消本次播放。';paint();}return;}
      if(closed||busy||!value)return;
      const initial=value,initialKey=activeKey(),version=++generation,controlSlot=document.getElementById('studio-control-slot');
      busy=true;preparing=['neutral','embarrassed','eat'].includes(operation);error='';actionError='';paint();
      const send=state=>{
        preparing=false;paint();
        const hello=host.observation().device?.identity?.hello;
        return api('/api/studio/command',{operation,command_id:'click-'+crypto.randomUUID(),connection_id:state.connection_id,target_id:operation==='enable'?hello?.device_id:state.target_id,boot_id:operation==='enable'?hello?.boot_id:state.boot_id,session_id:operation==='enable'?null:state.session_id});
      };
      try{const h=host.observation().device?.identity?.hello;if(!h)throw new Error('等待设备身份更新');
        const next=preparing?await readiness.play({operation,initial,read:budget=>api('/api/studio/state',null,budget),send,
          isCurrent:()=>!closed&&generation===version&&activeKey()===initialKey&&document.getElementById('studio-control-slot')===controlSlot,
          observe:state=>{accept(state);paint();}}):await send(initial);
        accept(next);error='';
      }catch(e){actionError=e.name==='AbortError'?'请求响应超时，请查看最新连接状态':e.message;}
      finally{busy=false;preparing=false;paint();if(!closed)void refresh();}
    }
    function init(){document.addEventListener('click',e=>{const b=e.target.closest('[data-e3c-action]');if(b&&!b.disabled)void command(b.dataset.e3cAction);});void refresh();timer=setInterval(()=>void refresh(),750);window.addEventListener('pagehide',()=>{closed=true;generation++;lastDisplay=null;clearInterval(timer);});}
    return Object.freeze({view,init,state:()=>({device:value?JSON.parse(JSON.stringify(value)):null,error,busy})});
  }
  window.DeskBotStudioE3CUI=Object.freeze({create});
})();
