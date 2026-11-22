/* Daily settings share the established USB owner; polling only reads host cache. */
(function(root){
  'use strict';
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const reasons={network_settings_busy:'正在处理连接设置',usb_connection_busy:'USB 正在处理其他操作',
    other_usb_port_connected:'请使用当前观察的头部端口',usb_command_in_progress:'等上一项 USB 请求完成后再试',
    head_usb_port_required:'请接好头部 USB，并刷新端口',network_settings_unavailable:'请用配套连接配置启动工作台',
    invalid_network_address:'请输入本地电脑地址，两个端口需各不相同',invalid_network_fields:'请核对地址和端口',
    invalid_network_revision:'请先读取桌宠设置',body_network_conflict:'设置已有更新，请重新读取后保存',
    body_network_save_uncertain:'请重启桌宠后读取实际保存的设置',body_network_save_failed:'保存未完成，请重新读取后再试',
    connected_device_or_build_mismatch:'请连接配套版本的桌宠',device_restarted_during_settings:'桌宠已重启，请重新读取',
    body_network_reply_timeout:'设备回读超时，请检查 USB 后重新读取',network_settings_failed:'本次操作未完成，请检查 USB 后重新读取',
    network_settings_reply_mismatch:'设备回读有变化，请重新读取',network_request_changed:'请重新读取后再操作'};
  function create(host){
    let data=null,message='',failed=false,submitting=false,polling=false,timer=null,inited=false,dirty=false,lastSettings='',pending=null;
    let draft={host:'',touch_port:'4318',expression_port:'4319'},port='';
    const slot=()=>document.getElementById('body-network-slot');
    const busy=()=>submitting||data?.task?.status==='running';
    const observation=()=>host.observation?.()||{};
    const selectedPort=()=>port||observation().device?.connection?.port||observation().selected||'';
    function view(){return `<section class="surface bn-card" aria-labelledby="bn-title">
      <header class="bn-header"><div><div class="eyebrow">DAILY CONNECTION</div><h2 id="bn-title">日常连接，一眼看清。</h2><p>桌宠连 Wi-Fi 传递摸头和表情，小智负责语音；修改电脑地址时接上 USB。</p></div><a class="bn-companion" href="http://127.0.0.1:4311/companion" target="_blank" rel="noopener">打开陪伴页 ↗</a></header>
      <div class="bn-services" aria-label="分项连接状态">${[['device','桌宠 · USB 观察'],['touch','摸头服务'],['expression','表情服务'],['xiaozhi','小智生活工具']].map(([id,title])=>`<div><span>${title}</span><strong id="bn-${id}" data-state="checking">正在读取</strong></div>`).join('')}</div>
      <div class="bn-settings"><div class="bn-address"><h3>电脑服务地址</h3><p>电脑与桌宠连接同一个 Wi-Fi。换网络后，在这里更新电脑地址。</p><form id="bn-form">
        <label>电脑地址<input id="bn-host" name="host" autocomplete="off" spellcheck="false" maxlength="63" placeholder="例如 192.168.1.10" required></label>
        <details class="bn-ports"><summary>端口设置</summary><div><label>摸头端口<input id="bn-touch_port" name="touch_port" type="number" min="1" max="65535" required></label><label>表情端口<input id="bn-expression_port" name="expression_port" type="number" min="1" max="65535" required></label></div></details>
        <div class="bn-actions"><label class="bn-usb">头部 USB<select id="bn-port" aria-label="头部 USB 端口"><option value="">等待头部连接</option></select></label><button class="button" type="button" id="bn-refresh" data-e3a-action="refresh">刷新端口</button><button class="button" type="button" data-bn-action="get">读取设置</button><button class="button button-primary" type="submit" id="bn-save">保存到桌宠</button></div>
      </form></div><aside class="bn-summary"><span>设备上次回读</span><strong id="bn-saved">尚未读取</strong><dl><div><dt>保存修订</dt><dd id="bn-revision">—</dd></div><div><dt>设备采用</dt><dd id="bn-applied">—</dd></div><div><dt>设置来源</dt><dd id="bn-source">—</dd></div></dl><button class="button" data-bn-action="reset">恢复随固件提供的地址</button><button class="button" data-bn-action="recommended">填入配套电脑地址</button></aside></div>
      <footer class="bn-footer"><p id="bn-feedback" role="status" aria-live="polite">正在读取连接状态…</p><button class="button" id="bn-retry" data-bn-action="retry" hidden>核对上次操作</button><span>设置通过 USB 保存，日常数据通过 Wi-Fi 传递。</span></footer></section>`;}
    function text(id,value){const el=document.getElementById(id);if(el&&el.textContent!==value)el.textContent=value;}
    function paint(){
      if(!slot())return;
      const obs=observation(),connection=obs.device?.connection,hello=obs.device?.identity?.hello;
      const confirmed=connection?.status==='connected'&&obs.device?.identity?.status==='confirmed';
      const matched=confirmed&&hello?.device_id===data?.binding?.device_id&&hello?.firmware?.app_elf_sha256===data?.binding?.firmware_sha256;
      const statuses={checking:'正在检查',ready:'可用',waiting:'等待小智连接',unavailable:'未启动'};
      for(const id of ['device','touch','expression','xiaozhi']){
        const state=id==='device'?(matched?'ready':connection?.status==='connected'?'waiting':'unavailable'):data?.services?.[id]||'checking';
        text('bn-'+id,id==='device'?(matched?'已连接':connection?.status==='connected'?'等待配套版本':'等待 USB'):statuses[state]||'等待更新');
        document.getElementById('bn-'+id).dataset.state=state;
      }
      const select=document.getElementById('bn-port'),ports=obs.ports||[];
      if(!ports.some(p=>p.port===selectedPort()))port=obs.selected||ports[0]?.port||'';
      const markup=ports.length?ports.map(p=>`<option value="${esc(p.port)}">${esc(p.port)} · 头部</option>`).join(''):'<option value="">等待头部连接</option>';
      if(select.dataset.options!==markup&&document.activeElement!==select){select.innerHTML=markup;select.dataset.options=markup;}
      select.value=selectedPort();
      const settings=data?.settings,key=JSON.stringify(settings);
      if(key!==lastSettings){
        lastSettings=key;
        if(settings&&!dirty)draft={host:settings.host,touch_port:String(settings.touch_port),expression_port:String(settings.expression_port)};
      }
      for(const name of Object.keys(draft)){
        const el=document.getElementById('bn-'+name);if(el.value!==draft[name])el.value=draft[name];
        el.disabled=busy()||!!pending||!data?.available;
      }
      text('bn-saved',settings?`${settings.host}:${settings.touch_port} / ${settings.expression_port}`:'尚未读取');
      text('bn-revision',settings?String(settings.revision):'—');
      text('bn-applied',settings?(settings.applied_revision===settings.revision?'已采用 · '+settings.revision:'等待采用'):'—');
      text('bn-source',settings?(settings.source==='saved'?'本机保存':'随固件提供'):'—');
      select.disabled=busy()||!!pending;
      document.getElementById('bn-refresh').disabled=busy()||!!pending;
      const ready=!!data?.available&&!!selectedPort()&&!busy()&&!pending;
      for(const button of slot().querySelectorAll('[data-bn-action],#bn-save')){
        const action=button.dataset.bnAction;
        button.disabled=action==='retry'?!pending||busy():action==='recommended'?!data?.recommended||busy()||!!pending:!ready||((action==='reset'||button.id==='bn-save')&&!settings);
      }
      document.getElementById('bn-retry').hidden=!pending;
      const task=data?.task;
      let note=message||(data?.available?'接好 USB 后点击“读取设置”。':'请用配套连接配置启动工作台。');
      if(task?.status==='running')note=({get:'正在读取桌宠设置…',set:'正在保存并核对设备采用…',reset:'正在恢复随固件提供的地址…'})[task.action];
      else if(!failed&&!pending&&task?.status==='failed')note=reasons[task.error]||'操作未完成，请重新读取。';
      else if(!message&&!pending&&task?.status==='completed')note=(task.action==='get'?'设置已读取。':settings?.applied_revision===settings?.revision?'设置已保存，设备已采用。':'设置已保存，等待设备采用。')+(task.observation_restored?' USB 观察已重新启动。':'');
      text('bn-feedback',note);document.getElementById('bn-feedback').dataset.error=String(failed||task?.status==='failed');
    }
    async function request(body){
      const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),10000);
      try{
        const response=await fetch('/api/body/network',{cache:'no-store',signal:controller.signal,...(body?{method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Studio':'body-network-v1'},body:JSON.stringify(body)}:{})});
        const value=await response.json();if(!response.ok){const error=Error(reasons[value.error]||'连接操作未完成，请重新读取。');error.rejected=true;throw error;}
        if(value.schema!=='deskbot.body_network_workbench/v1')throw Error('连接服务需要更新');return value;
      }finally{clearTimeout(timeout);}
    }
    function adopt(value){
      const ours=pending&&value.task?.request_id===pending.request_id;
      if(ours&&value.task.status!=='running'){
        if(value.task.status==='completed'){dirty=false;lastSettings='';message='';}
        pending=null;
      }
      data=value;
    }
    async function sync(){
      if(polling||submitting||!slot())return;polling=true;
      try{adopt(await request());failed=false;}catch{failed=true;message='电脑服务暂不可用，请恢复工作台后继续。';}
      finally{polling=false;paint();}
    }
    async function action(kind){
      if(busy())return;
      if(kind==='recommended'){
        const r=data?.recommended;if(r){draft={host:r.host,touch_port:String(r.touch_port),expression_port:String(r.expression_port)};dirty=true;message='已填入配套地址，点击保存后生效。';paint();}return;
      }
      if(kind!=='retry'&&pending)return;
      if(kind!=='retry'){
        const payload={request_id:'network-'+crypto.randomUUID().replaceAll('-',''),action:kind,port:selectedPort()};
        if(kind!=='get')payload.expected_revision=data?.settings?.revision;
        if(kind==='set')Object.assign(payload,{host:draft.host.trim(),touch_port:Number(draft.touch_port),expression_port:Number(draft.expression_port)});
        pending=payload;
      }
      submitting=true;message='正在提交…';failed=false;paint();
      try{adopt(await request(pending));}
      catch(error){failed=true;message=error.rejected?error.message:'正在核对上次操作，可点击“核对上次操作”接续。';if(error.rejected)pending=null;}
      finally{submitting=false;paint();void sync();}
    }
    function init(){
      if(inited)return;inited=true;
      document.addEventListener('click',e=>{const b=e.target.closest('[data-bn-action]');if(b&&!b.disabled)void action(b.dataset.bnAction);});
      document.addEventListener('submit',e=>{if(e.target.id==='bn-form'){e.preventDefault();void action('set');}});
      document.addEventListener('input',e=>{if(e.target.closest('#bn-form')&&e.target.name in draft){draft[e.target.name]=e.target.value;dirty=true;}});
      document.addEventListener('change',e=>{if(e.target.id==='bn-port')port=e.target.value;});
      timer=setInterval(()=>void sync(),1200);window.addEventListener('pagehide',()=>clearInterval(timer));
    }
    return Object.freeze({view,init,mount:()=>{paint();void sync();},state:()=>structuredClone({data,draft,dirty,pending,busy:busy()})});
  }
  root.DeskBotBodyNetwork=Object.freeze({create});
})(globalThis);
