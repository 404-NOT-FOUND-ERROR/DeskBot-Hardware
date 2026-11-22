(function(root){
  'use strict';
  const names={'airboat-face-v1':'新表情角色','grok-round-personal-v1':'圆团','xiaozhi-e145':'原有角色'};
  const semantics={neutral:'平静',happy:'开心',sad:'难过',embarrassed:'害羞',confused:'困惑',sleepy:'困倦'};
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function create(host){
    let busy=false,error='',attempted='',timer=0,closed=false,expanded=false;
    const complete=Object.entries(host.catalog?.complete_expressions||{}).map(([id,semantic])=>[semantic,host.catalog.expressions[id].label]);
    const live=()=>host.control().device;
    const fresh=v=>v?.roles?.status==='current'&&v.roles.reply&&v.roles.age_ms<3000;
    function view(){queueMicrotask(paint);return '<section class="device-roles" id="device-roles" aria-live="polite"></section>';}
    async function send(operation){
      if(busy)return;
      busy=true;error='';paint();
      try{await host.command(operation);}catch(e){error=e.message;}finally{busy=false;paint();}
    }
    function paint(){
      const slot=document.getElementById('device-roles');if(!slot)return;
      const state=host.control(),v=state.device,row=fresh(v)?v.roles.reply:null;
      const occupied=busy||state.busy||v?.host_command||v?.pending||v?.roles?.pending;
      const fullRole=row?.roles[row.active_index]==='airboat-face-v1';
      const playDisabled=occupied||row?.blocked||['queued','switching'].includes(row?.activation.status);
      const playButton=([id,name])=>`<button type="button" data-device-role-play="${id}" ${playDisabled?'disabled':''}>播放${esc(name)}</button>`;
      const label=index=>index>=0?names[row.roles[index]]||row.roles[index]:'等待播放';
      const failure=row&&(row.result==='rejected'||['failed','cancelled'].includes(row.activation.status))?({graph_changed:'已装包已变化，请刷新角色',player_unavailable:'播放器暂未就绪，请重试',frame_preparation_failed:'动画准备未完成，请重试',expression_interrupted:'其他表情已接管，可重新启用角色',request_expired:'本次切换已过期，请重试',session_changed:'连接已变化，请重新选择',target_replaced:'已采用新的切换目标'}[row.reason]||row.reason):'';
      const status=failure||(!v?.enabled?'连接 USB 并启用状态查询':!row?'正在读取设备角色':!row.loaded?'当前设备等待安装角色包':row.activation.status==='switching'?'正在完成过渡动画':row.activation.status==='queued'?'切换已排队':row.save_state==='failed'?'角色已切换，待保存':row.save_state==='saved'||row.save_state==='restored'?'选择已保存 · 重启后继续使用':'设备目录已读取');
      slot.innerHTML=`<header><div><span class="eyebrow">DEVICE · LIVE</span><h2>桌宠当前角色</h2><p>${esc(error||status)}</p></div><button type="button" data-device-role-refresh ${occupied||!v?.session_id?'disabled':''}>刷新设备角色</button></header>
      ${row?.loaded?`<div class="device-role-facts"><span>已生效 <strong>${esc(label(row.active_index))}</strong></span><span>切换目标 <strong>${esc(label(row.target_index))}</strong></span><span>播放阶段 <strong>${esc({none:'待播放',single:'单段',entry:'进入',loop:'持续',exit:'退出',held:'停留'}[row.phase])}</strong></span></div>
      <div class="device-role-choices">${row.roles.map((id,i)=>`<button type="button" data-device-role-index="${i}" aria-pressed="${row.target_index===i}" ${occupied?'disabled':''}><strong>${esc(names[id]||id)}</strong><small>${row.active_index===i?'当前生效':row.target_index===i?'正在切换':'点击启用'}${row.saved_index===i?' · 已保存':''}</small></button>`).join('')}</div>
      <div class="device-role-play">${Object.entries(semantics).map(playButton).join('')}</div>
      ${fullRole?`<details class="device-role-more" ${expanded?'open':''}><summary>全部 29 种表情</summary><div class="device-role-play">${complete.filter(([id])=>!Object.hasOwn(semantics,id)).map(playButton).join('')}</div></details>`:''}
      <p class="device-role-note">${fullRole?'完整新角色已常驻。原有角色保留电脑，连接后可重新安装。':'圆屏按设备已装包播放。'} 下方编辑与镜像调整保存为草稿，生成并安装后生效。</p>`:'<a href="#connection">打开设备连接 →</a>'}`;
      slot.querySelector('[data-device-role-refresh]').onclick=()=>send('read_roles');
      slot.querySelectorAll('[data-device-role-index]').forEach(b=>b.onclick=()=>send(`role.activate.${b.dataset.deviceRoleIndex}.${row.graph_sha256}`));
      slot.querySelectorAll('[data-device-role-play]').forEach(b=>b.onclick=()=>send(b.dataset.deviceRolePlay));
      const more=slot.querySelector('.device-role-more');if(more)more.ontoggle=()=>{expanded=more.open;};
    }
    function update(){
      if(closed)return;
      const v=live(),key=v?.enabled&&v.session_id?[v.connection_id,v.target_id,v.boot_id,v.session_id].join('|'):'';
      if(!key)attempted='';
      if(key&&attempted!==key&&!host.control().busy&&!v.host_command&&!v.pending&&v.catalog?.status!=='reading'){
        attempted=key;void send('read_roles');
      }
      paint();
    }
    function init(){update();timer=setInterval(update,750);addEventListener('pagehide',()=>{closed=true;clearInterval(timer);});}
    return {view,mount:paint,init,state:()=>({busy,error})};
  }
  root.DeskBotDeviceRolesE3f9d={create};
})(globalThis);
