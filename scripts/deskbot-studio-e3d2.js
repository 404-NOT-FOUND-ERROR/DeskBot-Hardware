/* Device directory reader. It uses the existing control bridge and USB session. */
(()=>{
  'use strict';
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const messages={not_requested:'点击读取，核对桌宠当前挂载的资源。',reading_pages:'正在分页读取；收齐后核对完整摘要。',verified:'目录完整，摘要已核对。',page_timeout:'这一页未收到回复，请重新读取。',invalid_page:'本次目录未完成核对，请重新读取。',connection_lost:'USB 已断开，实时目录已清空。',identity_changed:'桌宠已重启，请重新启用状态查询。',identity_unavailable:'等待桌宠身份更新。',query_stopped:'状态查询已停止，实时目录已清空。',session_unavailable:'设备会话已结束，请重新启用状态查询。'};
  function create(data,host){
    const saved=data.installed_directory.manifest;
    let busy=false,error='',timer,pending=null,lastKey='';
    function view(){return `<section class="surface e3d2-live" aria-label="设备实时目录"><div class="e3d2-heading"><div><div class="eyebrow">USB DEVICE DIRECTORY · E3d-2</div><h2>核对桌宠当前装了什么。</h2><p>沿用已连接的 USB 会话，读取当前资源、语义和附带动作。</p></div><span class="e3d-tag" id="e3d2-status" role="status">等待会话</span></div><div class="e3d2-path"><span>当前 USB 会话</span><i></i><span>分页收齐</span><i></i><span>摘要核对</span><i></i><span>设备实时目录</span></div><div class="e3d2-toolbar"><div><strong id="e3d2-summary">先启用状态查询</strong><p id="e3d2-message">连接 USB 后，在上方启用设备状态查询。</p></div><button class="button button-primary" data-e3d2-read disabled>读取实时目录</button></div><div class="e3d2-progress" role="progressbar" aria-label="目录读取进度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><span></span></div><div class="e3d2-facts"><div><span>设备内容</span><b id="e3d2-count">尚未读取</b></div><div><span>与 E1.18 快照对照</span><b id="e3d2-comparison">等待完整目录</b></div><div><span>表达接口</span><b id="e3d2-expressions">等待查询</b></div></div><details class="e3d2-items"><summary>目录条目与资源指纹</summary><div id="e3d2-entries"><p>完整读取后显示。</p></div></details><p class="e3d2-note">上方目录来自已验收的资源快照；这里的数据来自当前设备会话。目录读取与播放能力分别显示。</p></section>`;}
    function model(){
      const state=host.control(),v=state?.device,body=host.observation()?.device;
      const hello=body?.identity?.status==='confirmed'?body.identity.hello:null;
      const connected=body?.connection?.status==='connected';
      const identity=Boolean(hello&&v?.target_id===hello.device_id&&v?.boot_id===hello.boot_id);
      const active=Boolean(connected&&identity&&!state.error&&v?.enabled&&v?.session_id);
      const directory=active?v.catalog:null;
      const live=Boolean(directory?.status==='complete'&&directory.manifest&&
        directory.target_id===v.target_id&&directory.boot_id===v.boot_id&&directory.session_id===v.session_id);
      return {v,state,active,directory,live};
    }
    const text=(id,value)=>{const node=document.getElementById(id);if(node&&node.textContent!==String(value))node.textContent=String(value);};
    function paint(){
      const slot=document.getElementById('live-directory-slot');if(!slot)return;
      const {v,state,active,directory,live}=model();
      if(pending&&(v?.last_command?.command_id===pending.id||Date.now()-pending.at>5000))pending=null;
      const reading=active&&directory?.status==='reading',count=directory?.received_entries||0,total=directory?.total;
      const waiting=busy||Boolean(pending)||Boolean(v?.host_command);
      text('e3d2-status',live?'设备实时目录':reading?'正在读取':active?'会话已就绪':'等待会话');
      text('e3d2-summary',live?'已收齐并核对 '+count+' 个条目':reading?`已收到 ${count}${total?' / '+total:''} 个条目`:active?'准备读取当前目录':'先启用状态查询');
      text('e3d2-message',error||state?.error||(!active?'连接 USB 后，在上方启用设备状态查询。':messages[directory?.reason]||'点击读取，核对桌宠当前挂载的资源。'));
      text('e3d2-count',live?`${count} 个条目 · 当前会话`:reading?`${count}${total?' / '+total:''} 个条目`:'尚未读取');
      text('e3d2-comparison',live?(directory.manifest.catalog_id===saved.catalog_id?'与已验收快照一致':'目录版本不同，可查看条目'):'等待完整目录');
      text('e3d2-expressions',active&&v?.display?(v.display.expression_semantics??['neutral']).map(id=>({neutral:'平静',embarrassed:'害羞',eat:'进食'}[id]||id)).join(' · ')||'暂无可请求表情':'等待查询');
      const button=slot.querySelector('[data-e3d2-read]');
      if(button){button.disabled=!active||reading||waiting;button.textContent=waiting?'正在发送…':reading?'正在读取…':directory?.status==='complete'||directory?.status==='failed'?'重新读取':'读取实时目录';}
      const progress=slot.querySelector('.e3d2-progress'),percent=live?100:total?Math.round(count/total*100):0;
      if(progress){progress.setAttribute('aria-valuenow',String(percent));progress.firstElementChild.style.width=percent+'%';progress.classList.toggle('is-reading',reading);}
      slot.querySelector('.e3d2-live')?.classList.toggle('is-verified',live);
      const key=live?directory.manifest.catalog_id+':'+directory.session_id:'empty';
      const entries=document.getElementById('e3d2-entries');
      if(entries&&(lastKey!==key||!entries.dataset.ready)){
        lastKey=key;entries.dataset.ready='true';
        entries.innerHTML=live?`<dl class="e3d2-digests"><div><dt>目录</dt><dd>${esc(directory.manifest.catalog_id)}</dd></div><div><dt>资源包</dt><dd>${esc(directory.manifest.package_sha256)}</dd></div></dl><div class="e3d2-table">${directory.manifest.entries.map(row=>`<div><span>${esc(row.kind)}</span><b>${esc(row.id)}</b><code>${esc(row.resource_id || row.implicit_action_id || (row.wire_value ?? row.mirror))}</code></div>`).join('')}</div>`:'<p>完整读取后显示。</p>';
      }
    }
    async function read(){
      const {v,active}=model();if(!active||busy||pending)return;
      busy=true;error='';paint();
      const id='click-'+crypto.randomUUID(),abort=new AbortController(),limit=setTimeout(()=>abort.abort(),4000);
      try{
        const response=await fetch('/api/studio/command',{method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Studio':'e3c6'},signal:abort.signal,body:JSON.stringify({operation:'read_catalog',command_id:id,connection_id:v.connection_id,target_id:v.target_id,boot_id:v.boot_id,session_id:v.session_id})});
        const result=await response.json();if(!response.ok)throw new Error(result.error||'请求未完成');
        pending={id,at:Date.now()};
      }catch(e){error=e.name==='AbortError'?'请求响应超时，请查看会话状态后再试。':e.message;}
      finally{clearTimeout(limit);busy=false;paint();}
    }
    function init(){document.addEventListener('click',event=>{const button=event.target.closest('[data-e3d2-read]');if(button&&!button.disabled)void read();});paint();timer=setInterval(paint,400);window.addEventListener('pagehide',()=>clearInterval(timer));}
    return Object.freeze({view,init,state:()=>{const m=model();return {live:m.live,status:m.directory?.status||'unavailable',manifest_id:m.live?m.directory.manifest.catalog_id:null,entries:m.live?m.directory.manifest.entries.length:0,error};}});
  }
  window.DeskBotStudioE3D2UI=Object.freeze({create});
})();
