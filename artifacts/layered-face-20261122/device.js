/* Send the selected layered semantic through the workbench's existing owner. */
'use strict';
window.DeskBotFaceDevice={mount({report,semantic,labels}){
  if(location.protocol!=='http:'||location.hostname!=='127.0.0.1')return;
  const panel=document.createElement('div');panel.id='device-controls';
  panel.innerHTML='<nav><button id="device-play" disabled>播放到圆屏</button><button id="device-cancel" disabled>圆屏收尾</button><a href="/#connection" target="_top">连接圆屏 →</a></nav><p id="device-status" role="status">正在读取圆屏状态…</p>';
  document.querySelector('#label').parentElement.append(panel);
  const play=panel.querySelector('#device-play'),cancel=panel.querySelector('#device-cancel'),status=panel.querySelector('#device-status');
  let state=null,busy=false,closed=false,roleKey='',lastReceipt='',outcome='';
  async function api(path,value){
    const response=await fetch(path,{method:value?'POST':'GET',cache:'no-store',signal:AbortSignal.timeout(5000),
      ...(value?{headers:{'Content-Type':'application/json','X-DeskBot-Studio':'e3c6'},body:JSON.stringify(value)}:{})});
    const body=await response.json();if(!response.ok)throw Error(body.error||'连接状态已变化，请稍后再试');return body;
  }
  function matches(s){const r=s?.roles;return r?.status==='current'&&r.reply?.loaded&&r.reply.graph_sha256===report.graph_sha256;}
  function available(s){return s?.can_submit&&matches(s)&&!s.host_command&&!s.pending;}
  function paint(){
    play.disabled=cancel.disabled=busy||!available(state);
    if(busy){status.textContent='正在发送到圆屏…';return;}
    if(!state?.enabled||!state?.session_id){status.textContent='在连接页接通圆屏并启用状态查询。';return;}
    if(!matches(state)){status.textContent=state.roles?.reply?'圆屏角色与此预览不同，请安装当前角色包。':'正在核对圆屏角色…';return;}
    if(!state.can_submit){status.textContent='请让桌宠回主页待机，再播放表情。';return;}
    const receipt=(state.receipts||[]).filter(r=>r.target_id===state.target_id&&r.boot_id===state.boot_id&&r.session_id===state.session_id).at(-1);
    if(receipt&&receipt.request_id+':'+receipt.status!==lastReceipt){
      lastReceipt=receipt.request_id+':'+receipt.status;
      outcome=receipt.status==='dispatched'?'圆屏已接收，可继续切换或收尾。':
        receipt.status==='accepted'?'圆屏已受理。':receipt.status==='busy'?'圆屏正在处理，请稍后再试。':'圆屏已返回处理结果。';
    }
    status.textContent=state.host_command||state.pending?'正在等待圆屏回执…':outcome||'圆屏已连接，可播放所选表情。';
  }
  async function command(operation,s){
    return api('/api/studio/command',{operation,command_id:'face-'+crypto.randomUUID(),
      connection_id:s.connection_id,target_id:s.target_id,boot_id:s.boot_id,session_id:s.session_id});
  }
  async function refresh(){
    if(closed)return;
    try{
      state=await api('/api/studio/state');
      const key=state.session_id?[state.connection_id,state.boot_id,state.session_id].join(':'):'';
      if(!busy&&key&&roleKey!==key&&!state.host_command&&!state.pending){
        outcome='';lastReceipt='';await command('read_roles',state);roleKey=key;
      }
      paint();
    }catch{state=null;play.disabled=cancel.disabled=true;status.textContent='工作台连接待恢复。';}
    if(!closed)setTimeout(refresh,600);
  }
  async function send(operation){
    if(busy)return;busy=true;paint();
    try{
      const fresh=await api('/api/studio/state');
      if(!available(fresh))throw Error('请等待圆屏回到主页待机，角色核对完成后再试。');
      if(!(fresh.display?.expression_semantics||[]).includes(operation))throw Error('当前圆屏尚未提供这个表情。');
      state=await command(operation,fresh);outcome='已发送'+(operation==='neutral'?'收尾':labels[operation]||operation)+'，等待圆屏回执…';
    }catch(error){outcome=error.message;}
    finally{busy=false;paint();}
  }
  play.onclick=()=>send(semantic());cancel.onclick=()=>send('neutral');
  window.addEventListener('pagehide',()=>{closed=true;},{once:true});refresh();
}};
