(function(){
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const statusNames={accepted:'已受理',dispatched:'已提交执行',busy:'忙碌，未执行',rejected:'请求未受理',interrupted:'提交前中断',expired:'提交前过期',duplicate:'返回原结果'};
  const reasonNames={queued_for_owner:'等待表现模块处理',submitted_to_display:'已提交给显示模块',submitted_to_base:'已提交给底座模块',existing_owner_or_request:'对话或其他任务正在占用',base_unavailable:'底座当前离线',deadline_before_dispatch:'超过等待期限，未提交执行',owner_changed_before_dispatch:'表现模块状态已变化',base_disconnected_before_dispatch:'提交前底座断开',replayed_receipt:'同一请求编号只执行一次',request_id_conflict:'相同编号携带了不同内容',boot_changed:'设备已重启，旧请求不再执行',session_changed:'会话已变化，请重新同步',sequence_not_increasing:'请求序号已过期',catalog_or_role_changed:'资源目录或角色版本已变化',unsupported_expression_or_action:'目录中没有这项可请求能力',idempotency_window_full:'本轮请求记录已满，开始新的模拟启动周期'};
  const options={normal:'正常响应',busy:'对话占用',stall:'调度延迟',base_offline:'底座离线'};
  function create(data,host){
    const simulator=DeskBotStudioE2CCore.create(data.expression_catalog);
    const catalog=simulator.catalog();
    let kind='play_expression',selected='surprised',lastRequest=null,clientStatus='',lastFingerprint='',timer=0;
    const selections={play_expression:'surprised',play_action:'look_around'};
    function state(){return {...simulator.state(),last_request:lastRequest?JSON.parse(JSON.stringify(lastRequest)):null,client_status:clientStatus};}
    function notify(){lastFingerprint=JSON.stringify(simulator.state());host.render();}
    function requestRows(){return kind==='play_expression'?catalog.expressions.filter(r=>r.requestable).map(r=>({id:r.semantic_id,label:r.label})):catalog.actions.map(r=>({id:r.action_id,label:r.label}));}
    function current(){return kind==='play_expression'?catalog.expressions.find(r=>r.semantic_id===selected):catalog.actions.find(r=>r.action_id===selected);}
    function request(){
      const command=kind==='play_expression'?{kind,semantic_id:selected}:{kind,action_id:selected};
      lastRequest=simulator.makeRequest(command);
      const result=simulator.submit(lastRequest);
      clientStatus=result.delivered?'':'模拟连接已断开。重连后可重发原请求。';
      notify();
    }
    function preview(){
      const value=current();
      if(kind==='play_expression')return `<div class="sim-screen">${host.canvas(value.resource_id,{semanticId:value.semantic_id,animate:true})}</div><strong>${esc(value.label)}</strong><span class="mono small muted">${esc(value.resource_id)}</span>`;
      return `<div class="sim-motion" aria-label="${esc(value.label)}动作示意"><div class="sim-motion-pet"><i></i><i></i></div><span>动作示意</span></div><strong>${esc(value.label)}</strong><span class="small muted">底座内置动作 · ${value.wire_value}</span>`;
    }
    function history(s){
      if(!s.receipts.length)return '<div class="sim-empty">连接模拟器，发送第一条表达请求。<small>这里会展示每一步结果。</small></div>';
      return [...s.receipts].reverse().map(event=>event.message_type==='base_fact'?`<li class="sim-event fact"><span class="sim-event-dot"></span><div><strong>收到“动作完成”事实</strong><p>原底座协议没有请求编号，因此单独展示。</p><small>未关联请求 · ${esc(event.fact)}</small></div><span class="sim-event-tag">事实</span></li>`:`<li class="sim-event ${event.status}"><span class="sim-event-dot"></span><div><strong>${esc(statusNames[event.status])}${event.status==='duplicate'?' · '+esc(statusNames[event.original_status]):''}</strong><p>${esc(reasonNames[event.reason]||event.reason)}</p><small>${esc(event.request_id)} · #${event.sequence}</small></div><span class="sim-event-tag">${esc(event.status)}</span></li>`).join('');
    }
    function view(){
      const s=state(),value=current();
      const result=[...s.receipts].reverse().find(r=>r.message_type==='receipt'&&r.request_id===lastRequest?.request_id);
      const stage=result?.status;
      const running=s.pending?.request_id===lastRequest?.request_id;
      const route=['目录匹配','请求编号','模拟调度','执行回执'];
      const rowLabel=kind==='play_expression'?'表情':'动作';
      return `<div class="page-heading"><div><div class="eyebrow">CONNECTION LAB · E2c</div><h1>发出意图，看见回应。</h1><p>用电脑模拟器走完一次表达请求，再把同一协议接到桌宠。</p></div><span class="chip chip-green">电脑模拟 · 当前设备快照 E1.18</span></div>
      <section class="surface sim-status"><div><span class="sim-connection-dot ${s.connected?'connected':''}"></span><strong>${s.connected?'模拟器已连接':'模拟器未连接'}</strong><p>${s.connected?'目录与最近结果已同步。':'点击连接，读取表达目录与当前状态。'}</p></div><div class="sim-status-actions"><button class="button button-primary" data-sim-action="${s.connected?'disconnect':'connect'}">${s.connected?'断开模拟连接':'连接模拟器'}</button><button class="button" data-sim-action="restart">模拟重启</button></div></section>
      <div class="sim-flow" aria-label="表达请求链路">${route.map((text,i)=>`<div class="sim-flow-step ${i===0||lastRequest&&i<2||stage&&i<3||stage&&stage!=='accepted'?'done':''} ${running&&i===2?'running':''}"><span>${String(i+1).padStart(2,'0')}</span><strong>${text}</strong>${i<3?'<i>→</i>':''}</div>`).join('')}</div>
      <div class="sim-grid"><section class="surface sim-composer"><div class="section-heading"><h2>试一次表达</h2><span class="small muted">模拟目标</span></div><div class="sim-choice"><label>类型<select id="sim-kind" class="select-control" aria-label="请求类型"><option value="play_expression" ${kind==='play_expression'?'selected':''}>表情语义</option><option value="play_action" ${kind==='play_action'?'selected':''}>底座动作</option></select></label><label>${rowLabel}<select id="sim-selected" class="select-control" aria-label="请求内容">${requestRows().map(r=>`<option value="${esc(r.id)}" ${selected===r.id?'selected':''}>${esc(r.label)} · ${esc(r.id)}</option>`).join('')}</select></label></div><div class="sim-preview"><span class="small muted">选择预览</span>${preview()}</div>
      <div class="sim-behavior"><span>${kind==='play_expression'&&value.implicit_action_id?'该语义在现有事件路由关联：'+esc(catalog.actions.find(a=>a.action_id===value.implicit_action_id)?.label):kind==='play_expression'?'使用设备快照中的动画映射':'执行模块：C61 底座'}</span><p>${kind==='play_expression'?'本次只提交表情资源。动作需单独请求，事件路由中的动作绑定另行处理。角色草稿在“发布”页生成新资源包。':'示意图用于查看动作请求流程；实际幅度由底座决定。'}</p></div>
      <button class="button button-primary sim-send" data-sim-action="send" ${!s.connected?'disabled':''}>${running?'再发一条，测试忙碌':'发送模拟请求'}<span>→</span></button></section>
      <section class="surface sim-results"><div class="section-heading"><h2>执行过程</h2><span class="chip ${stage==='dispatched'?'chip-green':''}">${running?'等待提交':stage?esc(statusNames[stage]):'等待请求'}</span></div><label class="sim-scenario">模拟情况<select id="sim-mode" class="select-control" aria-label="模拟情况">${Object.entries(options).map(([id,label])=>`<option value="${id}" ${s.mode===id?'selected':''}>${label}</option>`).join('')}</select></label>
      ${clientStatus?`<div class="package-message" role="status">${esc(clientStatus)}</div>`:''}<ol class="sim-timeline" aria-live="polite">${history(s)}</ol>
      <div class="sim-secondary"><button class="button button-small" data-sim-action="repeat" ${!lastRequest||!s.connected?'disabled':''}>重发同一请求</button><button class="button button-small" data-sim-action="timeout" ${!lastRequest?'disabled':''}>标记客户端超时</button><button class="button button-small" data-sim-action="fact" ${!s.connected?'disabled':''}>模拟动作完成事实</button></div><p class="small muted sim-evidence-note">“已提交执行”表示请求交给表现模块；循环表情持续播放。动作完成事实单独显示。</p></section></div>
      <section class="surface sim-directory"><div class="section-heading"><div><h2>这份目录有什么</h2><p class="small muted">${catalog.expressions.length} 个表情语义 · ${catalog.actions.length} 个现有动作 · 聆听由语音状态管理</p></div><button class="button button-small" data-sim-action="export-catalog">导出目录</button></div><div class="sim-metrics"><div><strong>${s.execution_count}</strong><span>模拟提交次数</span></div><div><strong>${s.duplicate_count}</strong><span>重复请求已去重</span></div><div><strong>${s.pending?'1':'0'} / 1</strong><span>等待中的请求</span></div><div><strong>0</strong><span>设备写入</span></div></div><details class="publish-evidence"><summary>目录、请求与回执</summary><div class="sim-json-actions"><button class="button button-small" data-sim-action="export-session">导出本轮记录</button></div><pre>${esc(JSON.stringify({catalog:{catalog_id:catalog.catalog_id,revision:catalog.revision,source:catalog.source,role_id:catalog.role_id,limits:catalog.limits},session:{boot_id:s.boot_id,session_id:s.session_id},request:lastRequest,result:result||null},null,2))}</pre></details></section>`;
    }
    function init(){
      document.addEventListener('click',async event=>{const button=event.target.closest('[data-sim-action]');if(!button||button.disabled)return;
        try{const action=button.dataset.simAction;
          if(action==='connect'){await DeskBotStudioE2CCore.verifyCatalog(catalog);simulator.connect();clientStatus='目录摘要校验通过，已同步模拟状态。';notify();}
          if(action==='disconnect'){simulator.disconnect();clientStatus='';notify();}
          if(action==='restart'){simulator.restart();clientStatus='模拟启动周期已更新，旧请求只会返回“设备已重启”。';notify();}
          if(action==='send')request();
          if(action==='repeat'){const result=simulator.submit(lastRequest);clientStatus=result.delivered?'':'连接未送达';notify();}
          if(action==='timeout'){simulator.clientTimeout(lastRequest.request_id);clientStatus='客户端等待超时，执行结果未知。重新连接会同步设备结果。';notify();}
          if(action==='fact'){simulator.baseFact();notify();}
          if(action==='export-catalog')host.download('deskbot-expression-catalog.json',catalog);
          if(action==='export-session')host.download('deskbot-expression-simulation.json',{schema:'deskbot.expression_simulation/v1',catalog_id:catalog.catalog_id,...state()});
        }catch(error){host.toast(error.message);}
      });
      document.addEventListener('change',event=>{
        if(event.target.id==='sim-kind'){selections[kind]=selected;kind=event.target.value;selected=selections[kind];notify();}
        if(event.target.id==='sim-selected'){selected=event.target.value;selections[kind]=selected;notify();}
        if(event.target.id==='sim-mode'){simulator.setMode(event.target.value);notify();}
      });
      timer=setInterval(()=>{simulator.advance();const fingerprint=JSON.stringify(simulator.state());if(fingerprint!==lastFingerprint){lastFingerprint=fingerprint;host.render();}},100);
      window.addEventListener('pagehide',()=>{clearInterval(timer);});
    }
    return Object.freeze({view,init,state});
  }
  window.DeskBotStudioE2CUI=Object.freeze({create});
})();
