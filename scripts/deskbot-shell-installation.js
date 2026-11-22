/* Owner confirmation and actual device progress share one concise card. */
(function(root){
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const phases={confirmed:'安装已确认',waiting:'等待就绪',submitting:'正在切换',first_frame:'新画面已生效',saved:'设备表情已保存',reconciling:'核对设备结果',retry_required:'可以接续',failed:'等待重试',superseded:'已接续新安装',needs_confirmation:'内容更新，请重新确认'};
  const reasons={owner_confirmed:'安装已确认',waiting_previous:'等上一项请求完成',pause_automatic:'等待自动模式暂停',speaking:'等这句话说完',wait_home:'请回到主页',player_busy:'请唤醒桌宠并回主页',session_required:'请连接桌宠并建立会话',read_roles:'读取设备角色',content_required:'请先安装匹配的角色包',binding_updated:'请保存并重新确认当前角色内容',device_changed:'请连接确认时的桌宠',state_reconciled:'已按设备当前状态核对',save_failed:'画面已生效，保存可重试',device_state_read:'设备状态已读取，可以接续'};
  function create(){
    let data=null,shell='',actor='',pending=null,busy=false,message='',timer=null,number=0;
    const host=()=>document.getElementById('shell-installation-root');
    function invalidate(){if(data?.client_sync)data={...data,client_sync:{...data.client_sync,verified:false,status:'checking'}};}
    async function request(action,value){
      const control=new AbortController(),timeout=setTimeout(()=>control.abort(),15000);
      try{
        const r=await fetch('/api/shell-installation/'+action,{cache:'no-store',signal:control.signal,...(value?{method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Studio':'shell-installation-v1'},body:JSON.stringify(value)}:{})});
        const body=await r.json();if(!r.ok){const e=Error(body.error||'安装状态读取失败');e.status=r.status;e.state=body.state;throw e;}return body;
      }finally{clearTimeout(timeout);}
    }
    function paint(){
      const el=host();if(!el)return;
      const row=data?.records.find(r=>r.installation_id===data.current),choice=data?.choices.find(c=>c.shell_id===shell),device=data?.device;
      if(!shell&&data?.choices.length)shell=row?.shell_id||data.choices[0].shell_id;
      if(!actor&&data?.character_id)actor=data.character_id;
      const expressionName=id=>data?.choices.find(c=>c.expression_role_id===id)?.role_name||id||'等待读取';
      const client=data?.client_sync,clientSaved=client?.verified===true&&client.installation_id===row?.installation_id;
      const clientLabel=clientSaved?'客户端已同步':client?.status==='retrying'?'客户端待重试':client?'等待客户端同步':'客户端接续采用';
      const html=`<section class="sr-card si-card"><header class="sc-section-title"><div><span class="sc-eyebrow">INSTALLED SHELL</span><h2>我换好外壳了</h2></div><span class="sc-chip">${esc(row?phases[row.phase]:'确认当前安装')}</span></header><p>选好实际装上的外壳，桌宠回到主页后采用对应表情。共同记忆与关系继续保留。</p>
        <div class="sr-controls"><label>装上的外壳<select id="si-shell" ${busy?'disabled':''}><option value="">选择已关联的外壳</option>${(data?.choices||[]).map(c=>`<option value="${esc(c.shell_id)}" ${c.shell_id===shell?'selected':''} ${!c.ready?'disabled':''}>${esc(c.name)} · ${esc(c.role_name)}${!c.ready?' · 待重新关联':''}</option>`).join('')}</select></label><label>${data?.character_id?'当前伙伴':'伙伴编号'}<input id="si-actor" ${data?.character_id?'type="hidden"':''} value="${esc(actor)}" maxlength="128" placeholder="填写客户端的伙伴编号" ${busy||data?.character_id?'disabled':''}>${data?.character_id?'<span class="sc-role-summary">沿用客户端的记忆与关系</span>':''}</label><button class="sc-primary" data-si-action="confirm" ${busy||!data?.choices.some(c=>c.shell_id===shell&&c.ready)||!actor.trim()||!device?'disabled':''}>确认已安装</button><button data-si-action="refresh" ${busy?'disabled':''}>重新读取</button></div>
        <div class="sr-flow"><div><small>主人确认</small><strong>${esc(row?.shell_name||'等待选择')}</strong></div><i>→</i><div><small>目标表情</small><strong>${esc(row?.role_name||'等待确认')}</strong></div><i>→</i><div><small>设备实际表情</small><strong>${esc(expressionName(device?.actual))}</strong></div><i>→</i><div><small>设备保存</small><strong>${esc(expressionName(device?.saved))}</strong></div></div>
        <p class="sr-note" role="status">${esc(message||data?.error||reasons[row?.reason]||phases[row?.phase]||'先保存外壳与角色的关联，再确认安装。')}</p>${row&&['failed','retry_required'].includes(row.phase)?'<button data-si-action="retry">接续这次安装</button>':''}<footer><span data-si-client-sync>${esc(clientLabel)}</span>${clientSaved&&client.last_receipt?.received_at?' · 接收于 '+esc(new Date(client.last_receipt.received_at).toLocaleTimeString('zh-CN')):''} · 人格与音色接续采用</footer></section>`;
      if(el._siHTML!==html){
        const focus=el.contains(document.activeElement)?document.activeElement.id:null;
        el.innerHTML=html;el._siHTML=html;if(focus)el.querySelector('#'+focus)?.focus();
      }
    }
    function schedule(){clearTimeout(timer);timer=setTimeout(()=>{if(host())sync();},1000);}
    async function sync(){
      if(busy)return;const serial=++number;
      try{const result=await request('state');if(serial!==number)return;data=result;message='';
        if(pending&&data.records.some(r=>r.installation_id===pending.installation_id))pending=null;}
      catch(e){if(serial===number){invalidate();message=e.name==='AbortError'?'读取超时，请重新读取':e.message;}}
      finally{if(serial===number){paint();schedule();}}
    }
    async function action(kind){
      if(kind==='refresh')return sync();if(busy||!data)return;
      const row=data.records.find(r=>r.installation_id===data.current),choice=data.choices.find(c=>c.shell_id===shell);
      if(kind==='confirm'&&(!choice?.ready||!data.device||!actor.trim()))return;
      busy=true;++number;clearTimeout(timer);invalidate();message='正在保存安装确认…';paint();
      try{
        if(kind==='confirm'){
          pending=pending||{installation_id:'installation-'+crypto.randomUUID(),character_id:actor.trim(),device_id:data.device.device_id,shell_id:shell,binding_sha256:choice.binding_sha256,plan_id:null};
          data=await request('confirm',{...pending,expected_revision:data.revision});pending=null;message='安装已确认，正在核对设备进度';
        }else if(kind==='retry'&&row){data=await request('retry',{installation_id:row.installation_id,expected_revision:data.revision});message='正在接续这次安装';}
      }catch(e){if(e.state)data=e.state;
        // A rejected request can be renewed; uncertain transport keeps its ID for deduplication.
        if(e.status===409&&e.state&&pending&&!e.state.records.some(r=>r.installation_id===pending.installation_id))pending=null;
        message=e.name==='AbortError'?'保存结果待确认，点“重新读取”核对':e.message;}
      finally{busy=false;paint();schedule();}
    }
    function init(){
      document.addEventListener('click',e=>{const b=e.target.closest('[data-si-action]');if(b&&!b.disabled)action(b.dataset.siAction);});
      document.addEventListener('change',e=>{if(e.target.id==='si-shell'){shell=e.target.value;pending=null;message='';paint();}});
      document.addEventListener('input',e=>{if(e.target.id==='si-actor'){actor=e.target.value;pending=null;const b=host()?.querySelector('[data-si-action="confirm"]');if(b)b.disabled=busy||!data?.device||!actor.trim()||!data.choices.some(c=>c.shell_id===shell&&c.ready);}});
    }
    return Object.freeze({init,mount:()=>{paint();if(host())sync();},state:()=>structuredClone({data,shell,actor,busy,message})});
  }
  root.DeskBotShellInstallation=Object.freeze({create});
})(globalThis);
