(function(){
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const age=v=>v==null?'暂无':v<1000?`${v} ms`:`${(v/1000).toFixed(1)} 秒前`;
  const states={unknown:'未知',starting:'启动中',configuring:'配置中',idle:'待机',connecting:'连接中',listening:'聆听',speaking:'说话中',upgrading:'升级中',activating:'激活中',audio_testing:'音频测试',fatal_error:'故障',invalid_state:'状态异常'};
  const links={disabled:'观察已停止',connecting:'正在打开串口',connected:'串口已打开',missing:'设备已断开',mismatch:'USB 设备已变化',busy:'串口暂不可用',error:'观察进程已停止'};
  const fresh={disabled:'未连接',disconnected:'未连接',waiting:'等待本体日志',fresh:'实时更新',stale:'数据已过期'};
  function create(host={}){
    let enabled=false,ports=[],selected='',snapshot=null,error='',timer=0,busy=false,polling=false;
    const state=()=>({enabled,ports:ports.map(({port,usb_id})=>({port,usb_id})),selected,error,
      device:snapshot?JSON.parse(JSON.stringify(snapshot)):null,busy});
    async function api(path,body){
      const abort=new AbortController(),limit=setTimeout(()=>abort.abort(),body?12000:5000);
      try{
        const response=await fetch(path,{...(body?{method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Studio':'e3a'},body:JSON.stringify(body)}:{cache:'no-store'}),signal:abort.signal});
        const data=await response.json();if(!response.ok)throw new Error(data.error||'本机服务暂不可用');return data;
      }finally{clearTimeout(limit);}
    }
    function paint(){
      const slot=document.getElementById('body-observer-slot');
      if(!slot||slot.contains(document.activeElement)&&document.activeElement.tagName==='SELECT')return;
      const open=slot.querySelector('details')?.open;
      slot.innerHTML=view();if(open)slot.querySelector('details').open=true;
    }
    async function refresh(discover=false){
      if(location.protocol!=='http:'||location.hostname!=='127.0.0.1'){
        error='设备观察请打开本机工作台。';paint();return;
      }
      if(polling||busy)return;polling=true;
      try{
        const [found,current]=await Promise.all([discover?api('/api/body/ports'):Promise.resolve(null),api('/api/body/state')]);
        if(busy)return;
        snapshot=current;enabled=current.connection.status!=='disabled';
        if(found){
          if(!found.available)throw new Error(found.reason==='serial_runtime_required'?'请使用已配置串口环境启动工作台':'串口观察环境暂不可用');
          ports=found.ports;
          if(!ports.some(row=>row.port===selected))selected=ports.length?ports[0].port:'';
        }
        error='';
      }catch(e){error=e.name==='AbortError'?'本机服务响应超时':e.message||'本机服务暂不可用';
        if(snapshot){snapshot={...snapshot,live:false,freshness:'disconnected',connection:{...snapshot.connection,status:'error',reason:'host_service_unavailable'}};}
        enabled=false;}
      finally{polling=false;paint();}
    }
    function view(){
      const html=baseView();
      return host.decorate?host.decorate(html,state()):html;
    }
    function baseView(){
      const d=snapshot,deviceStatus=d?.connection?.status||'disabled',freshness=d?.freshness||'disabled';
      const picked=ports.find(p=>p.port===selected);
      const memory=d?.internal_memory;
      const ev=(d?.events||[]).slice(-4).reverse();
      const events=ev.length?ev.map(e=>`<li><span>${esc(e.sequence.toString().padStart(2,'0'))}</span><strong>${e.kind==='application_state'?'状态变化':e.kind==='connection'?'观察连接':'新启动'}</strong><em>${esc(states[e.value]||links[e.value]||e.value)}</em></li>`).join(''):'<li class="e3a-empty">还没有观察到状态变化</li>';
      const select=ports.length?`<label>USB 串口<select id="e3a-port" ${busy?'disabled':''}>${ports.map(p=>`<option value="${esc(p.port)}" ${selected===p.port?'selected':''}>${esc(p.port)} · Espressif USB 控制台</option>`).join('')}</select></label>`:'<span class="e3a-muted">未发现 Espressif USB 控制台</span>';
      return `<section class="surface e3a-observer"><div class="e3a-head"><div><div class="eyebrow">BODY · READ ONLY</div><h2>本体状态</h2><p>读取已有 USB 控制台日志，不发送命令或改变设备状态。</p></div><span class="e3a-pill ${freshness==='fresh'?'is-live':''}"><i></i>${esc(fresh[freshness]||fresh.disconnected)}</span></div>
        <div class="e3a-actions">${select}<button class="button button-primary" data-e3a-action="${enabled?'disconnect':'connect'}" ${busy||(!enabled&&(!picked||!d?.connection))?'disabled':''}>${busy?'正在处理':enabled?'停止观察':'连接只读观察'}</button><button class="button" data-e3a-action="refresh" ${busy?'disabled':''}>刷新端口</button></div>
        ${error?`<p class="e3a-error" role="status">${esc(error)}</p>`:''}
        <div class="e3a-facts"><div><span>串口</span><strong>${esc(links[deviceStatus]||deviceStatus)}${d?.connection?.port?' · '+esc(d.connection.port):''}</strong></div><div><span>最后观察状态</span><strong>${esc(d?.application_state?states[d.application_state.value]||d.application_state.value:'尚未观察到')}</strong></div><div><span>内部可用内存</span><strong>${memory?`${(memory.free_internal_bytes/1024).toFixed(1)} KiB`:'等待日志'}</strong></div><div><span>最近日志</span><strong>${esc(age(d?.last_seen_age_ms))}</strong></div></div>
        <div class="e3a-bottom"><span>${d?.application_state?`状态更新 ${esc(age(d.application_state.age_ms))}`:'内存日志通常约 10 秒更新一次；状态在变化时记录'}</span><span>${d?.timeline_id?'当前观察时段已记录':enabled?'等待首条日志':''}</span></div>
        <details class="e3a-events"><summary>最近变化 <b>${ev.length}</b></summary><ol>${events}</ol></details>
        <p class="e3a-note">设备数据只在 30 秒内有新日志时标记为实时。表情请求模拟仍是独立的电脑模拟。</p></section>`;
    }
    async function connect(){
      busy=true;error='';paint();
      try{const row=ports.find(p=>p.port===selected);if(!row)throw new Error('先选择一个本体 USB 串口');snapshot=await api('/api/body/connect',{port:row.port,key:row.key});enabled=true;}
      catch(e){error=e.message||'连接未完成';}
      finally{busy=false;paint();}
    }
    async function disconnect(){
      busy=true;paint();
      try{snapshot=await api('/api/body/disconnect',{});enabled=false;error='';}
      catch(e){error=e.message||'断开未完成';}
      finally{busy=false;paint();}
    }
    function init(){
      document.addEventListener('change',e=>{if(e.target.id==='e3a-port')selected=e.target.value;});
      document.addEventListener('click',e=>{const b=e.target.closest('[data-e3a-action]');if(!b||b.disabled)return;
        if(b.dataset.e3aAction==='connect')void connect();else if(b.dataset.e3aAction==='disconnect')void disconnect();else void refresh(true);
      });
      void refresh(true);if(location.protocol==='http:'&&location.hostname==='127.0.0.1')timer=setInterval(()=>void refresh(),1000);
      window.addEventListener('pagehide',()=>clearInterval(timer));
    }
    return Object.freeze({view,init,state});
  }
  window.DeskBotStudioE3AUI=Object.freeze({create});
})();
