(function(){
  'use strict';
  const Core=DeskBotStudioReleaseCore;
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const D=JSON.parse(document.getElementById('studio-data').textContent);
  const mib=n=>(n/1048576).toFixed(2)+' MiB';
  const hex=n=>'0x'+n.toString(16).toUpperCase().padStart(8,'0');
  const chip=(v,kind='')=>`<span class="chip ${kind?'chip-'+kind:''}">${esc(v)}</span>`;
  async function digest(value){const raw=new TextEncoder().encode(Core.canonical(value));return [...new Uint8Array(await crypto.subtle.digest('SHA-256',raw))].map(v=>v.toString(16).padStart(2,'0')).join('');}
  function create(D,host){
    let connection='checking',message='',busy=false,report=null,links=null,source='',request=0;
    const local=location.protocol==='http:'&&location.hostname==='127.0.0.1';
    async function connect(){
      if(!local){connection='standalone';host.render();return;}
      try{const response=await fetch('/api/health',{cache:'no-store',signal:AbortSignal.timeout(5000)});if(!response.ok)throw Error('请启动本机打包服务');const h=await response.json();if(h.service!=='deskbot.studio.packager/v1'||h.catalog_id!==D.catalog_id||h.target_id!==D.target.id)throw Error('本机服务与目录版本不匹配');connection=h.ready?'ready':'missing';message=h.missing?.join('、')||'';}
      catch(error){connection='standalone';message=error.message;}
      host.render();
    }
    async function accept(value,downloadLinks=null){
      Core.validate(value,D);
      if(await digest(value.input)!==value.input_sha256)throw Error('角色输入摘要与报告内容不匹配');
      if(await digest({input_sha256:value.input_sha256,image_sha256:value.image.sha256,target:value.target})!==value.package_id)throw Error('资源包版本摘要不匹配');
      links=downloadLinks?Core.downloads(downloadLinks,value):null;report=value;source=downloadLinks?'本机生成':'导入报告';
    }
    async function generate(){
      if(busy||connection!=='ready')return;
      const draft=host.draft(),seq=++request;busy=true;message='';host.render();
      try{const response=await fetch('/api/package',{method:'POST',headers:{'Content-Type':'application/json','X-DeskBot-Studio':'e2b'},body:JSON.stringify(draft),signal:AbortSignal.timeout(30000)});const value=await response.json();if(!response.ok)throw Error(value.error||'资源包生成未完成');await accept(value.report,value.downloads);host.toast(Core.matches(report,host.draft(),D)?'资源包已生成，差异与实际容量已更新。':'生成完成；当前角色已修改，请重新生成。');}
      catch(error){message=error.message;host.toast(message);}
      finally{if(seq===request){busy=false;host.render();}}
    }
    function status(){return {connection,busy,current:Core.matches(report,host.draft(),D),report:report?JSON.parse(JSON.stringify(report)):null,source};}
    function view(){
      const draft=host.draft(),role=draft.roles.find(r=>r.id===draft.selected_role_id),estimate=host.budget(),changes=Object.entries(role.bindings).filter(([sid,name])=>name!==D.semantics[sid].resource_id).map(([semantic,after])=>({semantic,before:D.semantics[semantic].resource_id,after})),current=Core.matches(report,draft,D),stale=report&&!current;
      const budget=current?report.budget:{package_bytes:estimate.bytes,free_bytes:estimate.free,soft_limit_free_bytes:estimate.soft_free},bytes=budget.package_bytes;
      const state=current?'资源包已生成':busy?'正在生成资源包':stale?'草稿已更新，等待重新生成':estimate.disconnected?'还有表情等待接线':'准备生成资源包';
      const ready=connection==='ready',disabled=!ready||busy||!!estimate.disconnected;
      const generateButton=`<button class="button button-primary ${busy?'is-building':''}" data-package-action="generate" ${disabled?'disabled':''}>${busy?'<span class="package-spinner" aria-hidden="true"></span>':''}${busy?'正在校验与打包…':current?'重新生成资源包':'生成资源包'}</button>`;
      const downloads=current&&links?`<a class="button" href="${links['expression-assets-8m.bin']}" download>下载 8 MiB 资源镜像</a><a class="button button-quiet" href="${links['report.json']}" download>下载审计报告</a>`:'';
      const health=ready?'本机打包服务已就绪':connection==='checking'?'正在连接本机服务':connection==='missing'?'本机缺少资源：'+message:'使用本机服务生成资源包';
      return `<div class="page-heading"><div><div class="eyebrow">RELEASE · 草稿到资源包</div><h1>把选好的表达，变成资源包。</h1><p>当前角色单独打包；接线、圆屏布局和实际容量一起核对。</p></div>${chip(state,current?'green':'amber')}</div>`+
        `<section class="package-toolbar surface"><div><span class="package-health ${ready?'ready':''}"></span><strong>${esc(health)}</strong><p>${ready?'生成后即可下载镜像与审计报告。':local?'可重新连接服务，或导入已有审计报告。':'打开本机工作台，接着生成。当前草稿可用右上角导出。'}</p></div><div class="publish-actions">${ready?'':`<a class="button" href="http://127.0.0.1:8766/">打开本机工作台</a>`}<button class="button button-quiet" data-package-action="connect">重新连接</button>${generateButton}</div></section>
        ${message?`<div class="package-message" role="status">${esc(message)}</div>`:''}
        <div class="publish-grid"><section class="surface publish-overview"><div class="release-heading"><h2>${esc(role.name)}</h2>${chip(current?'实际包容量':estimate.exact?'已知方案容量':'草稿容量估算',current?'green':'')}</div><div class="capacity-big" data-package-bytes="${bytes}">${(bytes/1048576).toFixed(2)} <span>/ 8 MiB</span></div><div class="capacity-track" role="meter" aria-label="资源区占用" aria-valuenow="${bytes}" aria-valuemin="0" aria-valuemax="8388608"><div class="capacity-fill" style="width:${Math.min(100,bytes/8388608*100)}%"></div><span class="capacity-marker" aria-label="7 MiB 发布软上限"></span></div><div class="capacity-legend"><span>余量 ${mib(budget.free_bytes)}</span><span>建议发布上限 7 MiB</span></div><div class="release-row"><span>角色接线</span><strong>${changes.length} 处变化${estimate.disconnected?' · '+estimate.disconnected+' 处未接线':''}</strong></div><div class="release-row"><span>目标资源区</span><strong class="mono">${hex(D.target.assets.offset)}–${hex(D.target.assets.offset+D.target.assets.bytes)}</strong></div><div class="release-row"><span>存储配置</span><strong>N32R16 · 32 MiB Flash / 16 MiB PSRAM</strong></div><div class="release-row"><span>当前包状态</span><strong>${esc(current?report.package.bytes.toLocaleString()+' B · '+source:state)}</strong></div></section>
        <section class="surface release-steps"><h2>发布进度</h2>${[['表达草稿','29 个语义与当前角色接线',!estimate.disconnected],['资源包生成',current?'真实大小、成员差异与摘要已生成':'按当前角色生成可复现的资源包',current],['布局与资源检查',current?'眼部镜像、聆听、图标、字体和模型核对通过':'保留已有资源，核对新动画与圆屏布局',current],['设备交互验证','三分钟映射、对话、菜单与摸头',false],['资源部署与验收','现场备份、资源区写入、回读与实际体验',false]].map(([title,detail,done],i)=>`<div class="release-step ${done?'done':''}"><span class="step-dot">${done?'✓':i+1}</span><div><h3>${title}</h3><p>${detail}</p></div></div>`).join('')}</section></div>
        <section class="surface changes-panel"><div class="section-heading"><h2>接线变化</h2><span class="small muted">当前角色与 E1.18 对照</span></div>${changes.length?`<table class="changes-table"><thead><tr><th>表情</th><th>原连接</th><th>新连接</th></tr></thead><tbody>${changes.map(c=>`<tr><td>${esc(D.semantics[c.semantic].label)}</td><td class="was mono">${esc(c.before)}</td><td class="now mono">${esc(c.after||'未接线')}</td></tr>`).join('')}</tbody></table>`:'<p class="small muted">当前角色沿用已验收的映射，生成结果可与 E1.18 逐字节对照。</p>'}<div class="publish-actions">${generateButton}${downloads}<button class="button button-quiet" data-package-action="import">导入审计报告</button><button class="button button-quiet" data-action="handoff">导出交接清单</button><button class="button button-quiet" data-navigate="editor">去编辑接线</button></div>
        ${current?`<div class="package-check-grid">${[['28','原资源成员保留'],[String(report.members.added.length),'新增动画成员'],['29','语义与聆听核对'],['8 MiB','目标镜像大小']].map(([v,k])=>`<div><strong>${v}</strong><span>${k}</span></div>`).join('')}</div><details class="publish-evidence"><summary>资源包审计详情 · ${esc(report.package_id.slice(0,12))}</summary><div class="package-checks">${['已有成员字节一致','新资源尺寸与帧数匹配','圆屏布局与聆听保持一致','动作分支保持一致','包解码与重新打包一致'].map(t=>`<span>✓ ${t}</span>`).join('')}</div><p class="small muted">${source==='导入报告'?'报告已核对目录、内容和输入摘要；资源二进制在本机生成时核对。':'本机生成与往返检查已完成。'}实机按发布进度继续。</p><pre>${esc(JSON.stringify({package:report.package,image:report.image,members:report.members,player_evidence:report.player_evidence},null,2))}</pre></details>`:stale?'<p class="small muted package-stale">上一份报告对应之前的角色或接线。重新生成后更新实际容量和下载文件。</p>':''}
        <details class="publish-evidence"><summary>本机启动与发布配置</summary><p class="small muted">在项目目录运行下方命令，再打开本机工作台。</p><pre>python scripts/serve-deskbot-studio-e2b.py</pre><pre>${esc(JSON.stringify(D.target,null,2))}</pre></details></section>`;
    }
    document.addEventListener('click',event=>{const target=event.target.closest('[data-package-action]');if(!target||target.disabled)return;const action=target.dataset.packageAction;if(action==='generate')generate();if(action==='connect')connect();if(action==='import')document.getElementById('package-report-file').click();});
    function init(){document.getElementById('package-report-file').addEventListener('change',async event=>{const file=event.target.files[0];if(!file)return;try{if(file.size>1024*1024)throw Error('审计报告应小于 1 MiB');const value=JSON.parse(await file.text());await accept(value);host.toast(Core.matches(report,host.draft(),D)?'已载入当前角色的资源包报告。':'报告已载入；当前角色或接线与报告不同。');host.render();}catch(error){host.toast('报告导入未完成：'+error.message);}finally{event.target.value='';}});connect();}
    return {view,init,status};
  }
  window.DeskBotStudioReleaseUI=Object.freeze({create});
})();
