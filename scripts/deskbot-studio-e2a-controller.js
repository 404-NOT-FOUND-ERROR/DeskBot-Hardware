(function () {
  'use strict';
  const D=JSON.parse(document.getElementById('studio-data').textContent), Core=DeskBotStudioCore;
  const $=id=>document.getElementById(id), workspace=$('workspace');
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const names={overview:'总览',editor:'表达编排',library:'素材库',roles:'角色',publish:'发布'};
  const actionNames={shark_head:'摇头',shark_head_decay:'轻轻摇头',look_around:'好奇环顾',beat_swing:'节奏摇摆',cat_nuzzle:'蹭头'};
  const storeKey='deskbot.studio.e2a.'+D.catalog_id.slice(0,16);
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  const icons={
    overview:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    nodes:'<rect x="2" y="8" width="6" height="8" rx="1.5"/><rect x="16" y="2" width="6" height="7" rx="1.5"/><rect x="16" y="15" width="6" height="7" rx="1.5"/><path d="M8 12h3c3 0 1-6 5-6M8 12h3c3 0 1 6 5 6"/>',
    library:'<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M3 10h18M10 10v11M8 6.5h.01M12 6.5h.01"/>',
    roles:'<circle cx="9" cy="8" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2M16 5a3 3 0 0 1 0 6M18 15a5 5 0 0 1 3 5"/>',
    publish:'<path d="M12 15V3m-4 4 4-4 4 4M4 14v5a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5"/>',
    device:'<circle cx="12" cy="10" r="8"/><path d="M7 21h10M12 18v3M8 9v2m8-2v2"/>',
    'arrow-right':'<path d="M5 12h14m-5-5 5 5-5 5"/>',
    import:'<path d="M12 3v12m-4-4 4 4 4-4M4 17v3h16v-3"/>',
    export:'<path d="M12 16V4m-4 4 4-4 4 4M4 16v4h16v-4"/>',
    theme:'<path d="M20 15.5A9 9 0 0 1 8.5 4 9 9 0 1 0 20 15.5Z"/>',
    close:'<path d="m6 6 12 12M6 18 18 6"/>',
    play:'<path d="m9 5 11 7-11 7V5Z"/>',
    pause:'<path d="M8 5v14M16 5v14"/>',
    step:'<path d="m5 5 10 7-10 7V5Zm14 0v14"/>',
    search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/>',
    undo:'<path d="m8 4-5 5 5 5M3 9h11a6 6 0 0 1 0 12"/>',
    redo:'<path d="m16 4 5 5-5 5m5-5H10a6 6 0 0 0 0 12"/>',
    drag:'<path d="M8 5h.01M16 5h.01M8 12h.01M16 12h.01M8 19h.01M16 19h.01"/>',
    check:'<path d="m5 12 4 4L19 6"/>',
    plus:'<path d="M12 5v14M5 12h14"/>',
    reset:'<path d="M3 10a9 9 0 1 1 2 8M3 4v6h6"/>',
    info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.01"/>',
    link:'<path d="m9 15 6-6m-7 3-3 3a4 4 0 0 0 6 6l3-3m-2-12 3-3a4 4 0 0 1 6 6l-3 3"/>',
    copy:'<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>'
  };
  const icon=name=>`<svg viewBox="0 0 24 24" aria-hidden="true">${icons[name]||icons.info}</svg>`;
  function mountIcons(root=document){root.querySelectorAll('[data-icon]').forEach(el=>{el.innerHTML=icon(el.dataset.icon);});}
  const mib=n=>(n/1048576).toFixed(2)+' MiB';
  const kib=n=>(n/1024).toFixed(1)+' KiB';
  const label=id=>D.semantics[id]?.label||id;
  const chip=(text,kind='')=>`<span class="chip ${kind?'chip-'+kind:''}">${esc(text)}</span>`;
  const button=(text,action,kind='',extra='')=>`<button class="button ${kind}" data-action="${action}" ${extra}>${esc(text)}</button>`;
  let initial=D.initial, recoveryMessage='';
  try {const saved=localStorage.getItem(storeKey);if(saved)initial=Core.parse(saved,D);}catch(error){recoveryMessage='已载入当前素材目录；上一次草稿可通过文件重新导入。';}
  const editor=Core.create(D,initial);
  let view=ownView(location.hash.slice(1)),semantic='embarrassed',routeId='cloud-emotion',semanticSearch='',librarySearch='',libraryFilter='all';
  let playing=!reduced.matches,time=0,last=0,raf=0,toastTimer=0,viewTransition=null,canvasObserver=null,graphObserver=null;
  let offsets={},drag=null,dialogKind='',previewMode='draft';
  const imageCache=new Map(), pendingImages=new Map(),painted=new WeakMap();
  function ownView(id){return Object.hasOwn(names,id)?id:'overview';}
  function active(){return editor.active();}
  function resource(){return active().bindings[semantic];}
  function baseline(){return D.semantics[semantic].resource_id;}
  function displayedResource(){return view==='overview'&&previewMode==='baseline'?baseline():resource();}
  function toast(message){$('toast').textContent=message;$('toast').classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').classList.remove('show'),3200);}
  function save(){
    try{localStorage.setItem(storeKey,JSON.stringify(editor.exportDocument()));$('save-state').innerHTML='<span></span>已自动保存';}
    catch(error){$('save-state').textContent='草稿在本页';toast('浏览器未开启本地存储，可用“导出草稿”保存。');}
  }
  function roleSelect(){return `<select class="select-control" id="role-picker" aria-label="当前角色">${editor.snapshot().roles.map(r=>`<option value="${esc(r.id)}" ${r.id===active().id?'selected':''}>${esc(r.name)}${r.example?' · 示例':''}</option>`).join('')}</select>`;}
  function heading(eyebrow,title,subtitle,actions=''){
    return `<div class="page-heading"><div><div class="eyebrow">${eyebrow}</div><h1>${title}</h1><p>${subtitle}</p></div>${actions?`<div class="heading-actions">${actions}</div>`:''}</div>`;
  }
  function canvas(id,{animate=false,semanticId=semantic,layout,cls='',staticFrame=false}={}){
    const mode=layout||D.semantics[semanticId]?.layout_id||(D.assets[id]?.object_kind==='listen'?'listen_anim':'eye_anim');
    return `<canvas width="360" height="360" class="${cls}" data-resource="${esc(id||'')}" data-semantic="${esc(semanticId)}" data-layout="${mode}" data-animate="${animate}" data-static="${staticFrame}" aria-label="${esc(D.assets[id]?.label||'尚未接入动画')}圆屏参考预览"></canvas>`;
  }
  function timing(id,sid){const a=D.assets[id],s=D.semantics[sid];return {fps:a?.origin==='candidate'?a.fps:s?.fps||a?.fps||20,loop:a?.origin==='candidate'?a.loop:s?.loop??a?.loop};}
  function previewControls(range=false){const a=D.assets[displayedResource()];return `<div class="inline playback"><button class="icon-button" data-action="play" aria-label="${playing?'暂停':'播放'}动画">${icon(playing?'pause':'play')}</button><button class="icon-button" data-action="step" aria-label="下一帧">${icon('step')}</button>${range?`<input id="frame-control" aria-label="动画帧" type="range" min="0" max="${Math.max(0,(a?.frame_count||1)-1)}" value="0" style="accent-color:var(--accent);min-width:80px;flex:1">`:''}<span class="frame-readout small muted">01 / ${a?.frame_count||'—'}</span></div>`;}
  function overview(){
    const a=D.assets[previewMode==='baseline'?baseline():resource()],diff=editor.changes().length;
    const flow=[['01','外壳','待底座恢复','device-info'],['02','身份','识别已验证','device-info'],['03','角色','可编辑草稿','roles'],['04','语义','29 个表达','editor'],['05','资源','动画可预览','library'],['06','身体','待实时接入','device-info']];
    return heading('EXPRESSION WORKSPACE · 阶段二','让表达，一目了然。','从一个情绪到圆屏上的模样，把整条链路放在一起。',chip('本地创作空间','green'))+
      `<div class="workflow-rail" aria-label="完整产品链路">${flow.map(([n,title,state,target])=>`<button class="flow-stop ${n==='04'||n==='05'?'active':''}" ${target==='device-info'?'data-action="device-info"':`data-navigate="${target}"`}><span class="flow-number">${n}</span><span><strong>${title}</strong><small>${state}</small></span></button>`).join('')}</div>
      <div class="overview-grid"><section class="surface preview-stage"><div class="segmented preview-modes" aria-label="前后版本预览"><button data-action="preview-mode" data-mode="baseline" aria-pressed="${previewMode==='baseline'}">设备快照</button><button data-action="preview-mode" data-mode="draft" aria-pressed="${previewMode==='draft'}">角色草稿</button></div><div class="preview-copy">${chip('正在预览 · '+(previewMode==='baseline'?'设备快照':'角色草稿'),'green')}<h2>${esc(label(semantic))}</h2><p>${esc(semantic)}<br>${esc(a?.id||'尚未接线')}</p><div class="preview-meta"><span>${a?.frame_count||'—'} 帧</span><span>${a?timing(a.id,semantic).fps:'—'} FPS</span><span>${timing(a?.id,semantic).loop?'循环播放':'播放一次'}</span></div></div><div class="device-stage">${canvas(a?.id,{animate:true,cls:'screen'})}<span class="screen-caption">${icon('device')}360 × 360 · 圆屏参考</span></div><div class="preview-controls">${previewControls()}</div></section>
      <div class="summary-stack"><section class="surface summary-panel"><div class="section-heading"><h2>当前表达方案</h2>${button('切换','go-roles','button-quiet button-small')}</div><div class="summary-profile"><span class="role-avatar">${icon('roles')}</span><div><h3>${esc(active().name)}</h3><p>${active().example?'示例角色 · 本地草稿':'沿用设备表达，可从这里开始修改'}</p></div></div><div class="fact-row"><span>设备资源快照</span><strong>E1.18 · ${mib(D.release.rollback_reference.package_bytes)}</strong></div><div class="fact-row"><span>当前角色草稿</span><strong>${diff?diff+' 处接线变化':'沿用当前映射'}</strong></div><div class="fact-row"><span>已准备的新表情</span><strong>害羞 · 单眼镜像 ${chip('待上机','amber')}</strong></div></section><button class="next-panel" data-navigate="editor"><span><span class="eyebrow">NEXT · 开始创作</span><strong>试着换一种表情</strong></span>${icon('arrow-right')}</button></div></div>
      <div class="section-label"><h2>先看看这些表情</h2><p>切换预览，再进入节点编排</p></div><div class="quick-semantics">${['happy','embarrassed','sleepy','surprised'].map(id=>`<button class="quick-semantic" data-action="select-semantic" data-semantic="${id}" aria-pressed="${semantic===id}">${canvas(active().bindings[id],{semanticId:id,staticFrame:true})}<span><strong>${esc(label(id))}</strong><small>${esc(id)}</small></span>${icon('arrow-right')}</button>`).join('')}</div>`;
  }
  function semanticList(){const q=semanticSearch.toLowerCase();return Object.entries(D.semantics).filter(([id,s])=>(id+' '+s.label+' '+active().bindings[id]).toLowerCase().includes(q)).map(([id,s])=>`<button class="semantic-item ${id===semantic?'active':''}" data-action="select-semantic" data-semantic="${id}"><span>${esc(s.label)} <small>${esc(id)}</small></span>${active().bindings[id]!==s.resource_id?'<i class="changed-dot" aria-label="已修改"></i>':''}</button>`).join('')||'<div class="empty-state">没有找到这个表情</div>';}
  function nodeHead(kind){return `<div class="node-kind" data-drag-handle><span>${kind}</span>${icon('drag')}</div>`;}
  function editorView(){
    const s=D.semantics[semantic],current=resource(),a=D.assets[current],changed=current!==baseline(),route=D.routes.find(r=>r.id===routeId);
    const direct=route.outputs.some(x=>x.semantic===semantic),routeText=direct?'源码固定输出':route.outputs.length?'当前表情为手动预览':'情绪由服务动态选择';
    const shared=Object.entries(D.semantics).filter(([,v])=>v.resource_id===baseline());
    const action=s.action_id,stats=timing(current,semantic);
    return heading('EXPRESSION GRAPH','把意图，接成表情。','节点可以拖动。选择新动画时，旧连接与新连接会同时保留作对照。',roleSelect())+
      `<div class="editor-layout"><aside class="surface semantic-panel"><div class="search-wrap">${icon('search')}<input id="semantic-search" value="${esc(semanticSearch)}" placeholder="找一个表情" aria-label="搜索表情语义"></div><div class="semantic-list" id="semantic-list">${semanticList()}</div></aside><div class="editor-main"><div class="editor-toolbar"><div class="inline"><span class="small muted">入口</span><select class="select-control" id="route-picker" aria-label="触发入口">${D.routes.map(r=>`<option value="${esc(r.id)}" ${r.id===routeId?'selected':''}>${esc(r.title)}</option>`).join('')}</select></div><div class="inline"><button class="icon-button" data-action="undo" aria-label="撤销" ${editor.canUndo()?'':'disabled'}>${icon('undo')}</button><button class="icon-button" data-action="redo" aria-label="重做" ${editor.canRedo()?'':'disabled'}>${icon('redo')}</button>${button('恢复当前','restore','button-small')}</div></div>
      <section class="surface graph-surface"><div class="graph-toolbar"><div class="inline"><h2>${esc(label(semantic))}</h2>${chip(changed?'草稿已修改':'沿用设备快照',changed?'amber':'')}</div><button class="text-button inline small" data-action="reset-graph">${icon('reset')}整理画布</button></div><div class="graph-scroll"><div class="graph-world" id="graph-world"><svg class="graph-connections" id="graph-connections" aria-hidden="true"></svg><div class="node-grid">
      <article class="node node-trigger" id="node-trigger" data-node="trigger">${nodeHead('01 · 触发入口')}<i class="node-port port-out"></i><div class="node-body"><h3>${esc(route.title)}</h3><p>${esc(routeText)}</p></div></article>
      <article class="node node-semantic" id="node-semantic" data-node="semantic">${nodeHead('02 · 情绪语义')}<i class="node-port port-in"></i><i class="node-port port-out"></i><i class="node-port port-bottom"></i><div class="node-body"><h3>${esc(label(semantic))}</h3><p class="mono">${esc(semantic)}</p></div></article>
      <article class="node node-resource" id="node-resource" data-node="resource">${nodeHead('03 · 动画资源')}<i class="node-port port-in"></i><div class="node-canvas">${canvas(current,{animate:true})}</div><div class="node-body"><h3>${esc(a?.label||'等待接入动画')}</h3><p class="mono">${esc(current||'未接线')}</p>${button('替换动画','pick-resource','button-small')}</div></article>
      ${changed?`<article class="node node-old" id="node-old" data-node="old">${nodeHead('原连接 · 已断开')}<i class="node-port port-in port-old"></i><div class="node-canvas">${canvas(baseline(),{animate:true})}</div><div class="node-body"><p class="mono">${esc(baseline())}</p></div></article>`:''}
      <article class="node node-action" id="node-action" data-node="action">${nodeHead('独立分支 · 身体动作')}<i class="node-port port-in"></i><div class="node-body"><h3>${esc(actionNames[action]||'随设备当前行为')}</h3><p>${action?esc(action):'这个语义没有固定伴随动作'}<br>现有动作映射</p></div></article></div></div></div><div class="graph-note">${icon('info')}<span>${esc(routeText)} · 表情资源与身体动作分别接线</span></div></section>
      <div class="editor-bottom"><section class="surface detail-panel"><div class="section-heading"><h3>圆屏与播放</h3>${chip(s.layout_id==='eye_anim'?'双眼镜像':'聆听布局')}</div><div class="details-list"><div><span>原始画面</span><span>${a?`${a.frame_width} × ${a.frame_height}`:'—'}</span></div><div><span>播放</span><span>${stats.fps} FPS · ${stats.loop?'循环':'单次'} · ${a?.frame_count||'—'} 帧</span></div></div><div style="margin-top:12px">${previewControls(true)}</div></section><section class="surface detail-panel"><h3>替换会影响什么？</h3><p>当前只调整“${esc(label(semantic))}”在“${esc(active().name)}”里的动画连接。</p><p style="margin-top:8px">原资源 ${esc(baseline())} 在设备快照中由 ${shared.length} 个语义共用。</p><details class="route-details"><summary>查看入口与关联语义</summary><p>${esc(route.condition)}</p><p>${esc(route.note)}</p><div class="semantic-tags">${shared.map(([id])=>`<button data-action="select-semantic" data-semantic="${id}">${esc(label(id))}</button>`).join('')}</div></details></section></div></div></div>`;
  }
  function usedCount(id){return Object.values(active().bindings).filter(x=>x===id).length;}
  function assetCard(id,picker=false){const a=D.assets[id];return `<button class="asset-card" data-action="${picker?'choose-resource':'asset-detail'}" data-resource="${esc(id)}"><div class="asset-art">${a.origin==='candidate'?chip('新候选','amber'):usedCount(id)?chip(usedCount(id)+' 处使用','green'):''}${canvas(id,{semanticId:'',staticFrame:true})}</div><div class="asset-card-body"><strong>${esc(a.label)}</strong><p>${esc(id)}</p><div class="asset-stats"><span>${a.frame_count} 帧 · ${a.fps} FPS</span><span>${kib(a.bytes)}</span></div></div></button>`;}
  function libraryCards(){const q=librarySearch.toLowerCase();if(libraryFilter==='actions')return D.actions.filter(a=>(a.id+' '+actionNames[a.id]).toLowerCase().includes(q)).map(a=>`<button class="asset-card action-card" data-action="action-detail" data-motion="${a.id}"><div class="asset-art action-art">${icon('device')}<span>动作目录</span></div><div class="asset-card-body"><strong>${esc(actionNames[a.id])}</strong><p>${esc(a.id)}</p><div class="asset-stats"><span>现有底座动作</span><span>编号 ${a.wire_value}</span></div></div></button>`).join('')||'<div class="empty-state">没有找到这个动作</div>';const ids=Object.keys(D.assets).filter(id=>{const a=D.assets[id];return (libraryFilter==='all'||libraryFilter==='official'&&a.origin==='official'||libraryFilter==='candidate'&&a.origin==='candidate'||libraryFilter==='used'&&usedCount(id)>0)&&(id+' '+a.label).toLowerCase().includes(q);});return ids.map(id=>assetCard(id)).join('')||'<div class="empty-state">没有找到这类素材，试试其他名称。</div>';}
  function library(){return heading('RESOURCE LIBRARY','一眼看清，每一种表达。','动画素材、现有动作和它们的使用关系，在同一个目录里查看。',chip('19 个动画 · 5 个动作'))+
    `<div class="filter-bar"><div class="segmented" aria-label="素材类型">${[['all','全部动画'],['used','角色使用'],['official','官方'],['candidate','新候选'],['actions','身体动作']].map(([id,name])=>`<button data-action="filter-library" data-filter="${id}" aria-pressed="${libraryFilter===id}">${name}</button>`).join('')}</div><input class="input-control" id="library-search" value="${esc(librarySearch)}" placeholder="搜索名称或文件" aria-label="搜索动画素材"></div><div class="library-grid" id="library-grid">${libraryCards()}</div>`;}
  function rolesView(){const roles=editor.snapshot().roles;return heading('CHARACTER PROFILES','同一个身体，不同的表达。','每个角色保存自己的表情接线；从已有方案复制，再慢慢调整。',`<button class="button" data-action="clone-role">${icon('plus')}复制当前角色</button>`)+
    `<div class="role-grid">${roles.map(r=>`<button class="surface role-card" data-action="select-role" data-role="${r.id}" aria-pressed="${r.id===active().id}"><span class="role-avatar">${icon('roles')}</span>${chip(r.id===active().id?'正在编辑':r.example?'示例草稿':'默认方案',r.id===active().id?'green':'')}<h2>${esc(r.name)}</h2><p>${r.example?'角色表达示例，可复制和修改':'从已验收的设备表达开始'}</p><div class="role-stats"><span>29 个表情映射</span><span>${Core.differences(r,D).length} 处变化</span></div></button>`).join('')}</div>
    <section class="surface role-detail"><div class="role-form"><h2 style="font-size:15px;margin-bottom:20px">角色资料</h2><label for="role-name">显示名称</label><input class="input-control" id="role-name" maxlength="80" value="${esc(active().name)}"><div class="fact-row"><span>声音</span><strong>${esc(active().voice)}</strong></div><div class="fact-row"><span>外壳绑定</span><strong>后续接入客户端确认</strong></div><div class="fact-row"><span>保存位置</span><strong>当前浏览器 · 本地草稿</strong></div></div><div><h3>从表情开始定义这个角色</h3><p class="small muted" style="margin-top:9px">每个语义对应一个动画。用节点编排查看它的入口、圆屏效果和独立动作分支。</p><div class="role-map">${['happy','embarrassed','sleepy','surprised','confused','neutral'].map(id=>`<button class="button button-small" data-action="edit-semantic" data-semantic="${id}">${esc(label(id))}${icon('arrow-right')}</button>`).join('')}</div><div class="publish-actions"><button class="button button-primary" data-navigate="editor">${icon('nodes')}编辑角色表达</button></div></div></section>`;}
  function publishView(){
    const b=editor.budget(),changes=editor.changes(),stateLabels={baseline:'沿用已验收映射',verified_candidate:'匹配 E1.45 已审计候选',review_required:'草稿等待包审阅',over_budget:'调整资源后再发布',incomplete:'还有表情等待接线'};
    return heading('RELEASE REVIEW','先看清变化，再发布。','把草稿、容量、设备版本和下一步，放在一张清单里。',chip(stateLabels[b.state],b.state==='verified_candidate'?'green':b.changes?'amber':''))+
    `<div class="publish-grid"><section class="surface publish-overview"><div class="release-heading"><h2>${esc(active().name)}</h2>${chip(b.exact?'已知包大小':'资源估算',b.exact?'green':'amber')}</div><div class="capacity-big">${(b.bytes/1048576).toFixed(2)} <span>/ 8 MiB</span></div><div class="capacity-track" role="meter" aria-label="资源区占用" aria-valuenow="${Math.round(b.bytes)}" aria-valuemin="0" aria-valuemax="8388608"><div class="capacity-fill" style="width:${Math.min(100,b.bytes/8388608*100)}%"></div><span class="capacity-marker" aria-label="7 MiB 发布软上限"></span></div><div class="capacity-legend"><span>余量 ${mib(b.free)}</span><span>建议发布上限 7 MiB</span></div><div class="release-row"><span>设备记录</span><strong>App E1.42 · 资源 E1.18</strong></div><div class="release-row"><span>草稿变化</span><strong>${changes.length} 处接线${b.disconnected?' · '+b.disconnected+' 处未接线':''}</strong></div><div class="release-row"><span>活动资源分区</span><strong class="mono">0x01000000–0x01800000</strong></div><div class="release-row"><span>当前包状态</span><strong>${b.state==='verified_candidate'?'E1.45 包已准备，待实机验证':changes.length?'先导出草稿，进入包差异审阅':'设备基线资源快照'}</strong></div></section>
    <section class="surface release-steps"><h2>发布路径</h2>${[['表达草稿','选择资源，查看节点和圆屏效果',true],['包与播放检查',b.state==='verified_candidate'?'当前接线与 E1.45 审计包一致':'导出所选角色的接线与版本信息',b.state==='verified_candidate'],['设备交互验证','三分钟映射、对话、菜单与摸头',false],['资源写入与回读','现场备份，限定资源区部署',false],['实际体验验收','确认动画、触发行为与日常交互',false]].map(([title,detail,done],i)=>`<div class="release-step ${done?'done':''}"><span class="step-dot">${done?icon('check'):i+1}</span><div><h3>${title}</h3><p>${detail}</p></div></div>`).join('')}</section></div>
    <section class="surface changes-panel"><div class="section-heading"><h2>接线变化</h2><span class="small muted">比较当前角色草稿与 E1.18</span></div>${changes.length?`<table class="changes-table"><thead><tr><th>表情</th><th>原连接</th><th>新连接</th></tr></thead><tbody>${changes.map(c=>`<tr><td>${esc(label(c.semantic))}</td><td class="was mono">${esc(c.before)}</td><td class="now mono">${esc(c.after||'未接线')}</td></tr>`).join('')}</tbody></table>`:'<p class="small muted">当前方案沿用设备映射。可以从“害羞”开始，接入已准备的新候选。</p>'}<div class="publish-actions">${button('导出交接清单','handoff','button-primary')}<a class="button" href="../../artifacts/expression-e1-45-staging-20261104/index.html">查看 E1.45 资源包 ${icon('arrow-right')}</a>${button('去编辑接线','go-editor','button-quiet')}</div><details class="publish-evidence"><summary>版本与审阅依据</summary><pre>${esc(JSON.stringify({catalog_id:D.catalog_id,sources:D.sources,package_bytes:b.bytes,exact_package_size:b.exact,offline_draft:true,hardware_accessed:false},null,2))}</pre></details></section>`;
  }
  function render(){
    canvasObserver?.disconnect();graphObserver?.disconnect();
    $('breadcrumb-current').textContent=names[view];
    document.querySelectorAll('#main-nav a').forEach(a=>{if(a.dataset.view===view)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
    workspace.innerHTML=`<div class="page-enter" id="view-content">${({overview,editor:editorView,library,roles:rolesView,publish:publishView}[view])()}</div>`;
    last=0;mountIcons(workspace);observeCanvases();
    if(view==='editor'){
      for(const [id,offset] of Object.entries(offsets)){const n=$('node-'+id);if(n)n.style.transform=`translate(${offset.x}px,${offset.y}px)`;}
      graphObserver=new ResizeObserver(wires);graphObserver.observe($('graph-world'));requestAnimationFrame(wires);
    }
    syncPlayback();startRaf();
  }
  function navigate(id){
    const next=ownView(id);view=next;offsets={};time=0;
    try{history.replaceState(null,'','#'+next);}catch(error){location.hash=next;}
    if(!reduced.matches&&document.startViewTransition){viewTransition?.skipTransition();viewTransition=document.startViewTransition(render);viewTransition.finished.catch(()=>{});}else render();
  }
  function changed(message){save();render();toast(message);}
  function selectSemantic(id,goEditor=false){if(!Object.hasOwn(D.semantics,id))return;semantic=id;time=0;offsets={};if(goEditor)navigate('editor');else render();}
  function currentFrame(){const id=displayedResource(),a=D.assets[id];return a?Core.frameAt({...a,...timing(id,semantic)},time):0;}
  function syncPlayback(){
    document.querySelectorAll('[data-action="play"]').forEach(b=>{b.innerHTML=icon(playing?'pause':'play');b.setAttribute('aria-label',(playing?'暂停':'播放')+'动画');});
    const id=displayedResource(),a=D.assets[id],frame=currentFrame();
    document.querySelectorAll('.frame-readout').forEach(el=>{el.textContent=`${String(frame+1).padStart(2,'0')} / ${a?.frame_count||'—'}`;});
    if($('frame-control'))$('frame-control').value=String(frame);
  }
  function loadImage(id){
    if(imageCache.has(id)){const image=imageCache.get(id);imageCache.delete(id);imageCache.set(id,image);return Promise.resolve(image);}
    if(pendingImages.has(id))return pendingImages.get(id);
    const a=D.assets[id];if(!a?.atlas)return Promise.resolve(null);
    const promise=new Promise(resolve=>{const image=new Image();image.onload=()=>{imageCache.set(id,image);pendingImages.delete(id);const protectedIds=new Set([...document.querySelectorAll('canvas[data-animate="true"]')].map(c=>c.dataset.resource));for(const key of imageCache.keys()){if(imageCache.size<=8)break;if(!protectedIds.has(key))imageCache.delete(key);}resolve(image);};image.onerror=()=>{pendingImages.delete(id);toast('这份素材的预览未加载完成，可重新打开素材查看。');resolve(null);};image.src=a.atlas;});pendingImages.set(id,promise);return promise;
  }
  function draw(canvasEl,image){
    if(!canvasEl.isConnected)return;
    const id=canvasEl.dataset.resource,a=D.assets[id],ctx=canvasEl.getContext('2d'),sid=canvasEl.dataset.semantic;
    const seconds=canvasEl.dataset.static==='true'?.55:time;
    const n=a?Core.frameAt({...a,...timing(id,sid)},seconds):0,key=id+':'+n+':'+canvasEl.dataset.layout;
    if(painted.get(canvasEl)===key)return;
    painted.set(canvasEl,key);ctx.clearRect(0,0,360,360);ctx.fillStyle='#000';ctx.fillRect(0,0,360,360);ctx.save();ctx.beginPath();ctx.arc(180,180,180,0,Math.PI*2);ctx.clip();
    if(a&&image){const w=a.frame_width,h=a.frame_height,x=n%a.columns*w,y=Math.floor(n/a.columns)*h;
      if(canvasEl.dataset.layout==='eye_anim'){const cw=Math.min(w,340),top=Math.floor((360-h)/2)+10;ctx.drawImage(image,x,y,cw,h,10,top,cw,h);ctx.translate(350,0);ctx.scale(-1,1);ctx.drawImage(image,x,y,cw,h,0,top,cw,h);}
      else {const scale=canvasEl.dataset.layout==='raw'?Math.min(1,330/w,330/h):1,fw=w*scale,fh=h*scale;ctx.drawImage(image,x,y,w,h,(360-fw)/2,canvasEl.dataset.layout==='listen_anim'?25:(360-fh)/2,fw,fh);}
    }else{ctx.fillStyle='#9eb0a8';ctx.textAlign='center';ctx.font='16px system-ui';ctx.fillText(!a?'等待接入动画':!a.atlas?'本机版本可预览':'加载动画…',180,184);}
    ctx.restore();
  }
  function paintCanvas(c){const id=c.dataset.resource;if(imageCache.has(id)){draw(c,imageCache.get(id));return;}draw(c,null);if(D.assets[id]?.atlas)loadImage(id).then(image=>{painted.delete(c);draw(c,image);});}
  function observeCanvases(){
    canvasObserver?.disconnect();canvasObserver=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting){paintCanvas(entry.target);if(entry.target.dataset.static==='true')canvasObserver.unobserve(entry.target);}}, {rootMargin:'100px'});
    document.querySelectorAll('canvas[data-resource]').forEach(c=>{canvasObserver.observe(c);if(c.dataset.animate==='true')paintCanvas(c);});
  }
  function paintAnimated(){document.querySelectorAll('#workspace canvas[data-animate="true"],dialog[open] canvas[data-animate="true"]').forEach(c=>{const box=c.getBoundingClientRect();if(box.bottom>=0&&box.top<=innerHeight)paintCanvas(c);});syncPlayback();}
  function tick(now){raf=0;if(!playing||document.hidden)return;if(last)time+=Math.min(.25,(now-last)/1000);last=now;paintAnimated();raf=requestAnimationFrame(tick);}
  function startRaf(){if(playing&&!document.hidden&&!raf)raf=requestAnimationFrame(tick);}
  function pause(){playing=false;cancelAnimationFrame(raf);raf=0;last=0;syncPlayback();}
  function wires(){
    const world=$('graph-world'),svg=$('graph-connections');if(!world||!svg)return;
    const rect=world.getBoundingClientRect();svg.setAttribute('viewBox',`0 0 ${world.clientWidth} ${world.clientHeight}`);
    const connect=(from,to,cls='',direction='right')=>{const f=$(from),t=$(to);if(!f||!t)return '';const output=f.querySelector(direction==='vertical'?'.port-bottom':direction==='left'?'.port-in':'.port-out'),input=t.querySelector('.port-in');if(!output||!input)return '';const fr=output.getBoundingClientRect(),tr=input.getBoundingClientRect(),x1=fr.left+fr.width/2-rect.left,y1=fr.top+fr.height/2-rect.top,x2=tr.left+tr.width/2-rect.left,y2=tr.top+tr.height/2-rect.top;
      const bend=Math.max(20,Math.abs(x2-x1)*.5),sign=direction==='left'?-1:1,points=direction==='vertical'?`${x1} ${y1+20},${x2} ${y2-20}`:`${x1+bend*sign} ${y1},${x2-bend*sign} ${y2}`;return `<path class="${cls}" d="M ${x1} ${y1} C ${points},${x2} ${y2}"/>`;};
    const route=D.routes.find(r=>r.id===routeId),dynamic=!route.outputs.some(x=>x.semantic===semantic);
    svg.innerHTML=connect('node-trigger','node-semantic',dynamic?'wire-dynamic':'')+connect('node-semantic','node-resource',resource()!==baseline()&&!drag?'wire-new':'')+connect('node-semantic','node-action','wire-action','vertical')+connect('node-semantic','node-old','wire-old','left');
  }
  function showDialog(title,eyebrow,content,kind){
    $('dialog-title').textContent=title;$('dialog-eyebrow').textContent=eyebrow;$('dialog-content').innerHTML=content;dialogKind=kind;mountIcons($('studio-dialog'));if(!$('studio-dialog').open)$('studio-dialog').showModal();observeCanvases();paintAnimated();
  }
  function picker(){
    const ids=D.semantics[semantic].allowed_resources;
    showDialog('给“'+label(semantic)+'”换个模样','CHOOSE ANIMATION · 选择动画',`<p class="small muted" style="margin-bottom:18px">选择后会保留原动画作对照。当前角色：${esc(active().name)}</p><div class="library-grid">${ids.map(id=>assetCard(id,true)).join('')}</div>`,'picker');
  }
  function assetDetail(id){
    const a=D.assets[id];if(!a)return;
    const refs=Object.entries(active().bindings).filter(([,v])=>v===id);
    showDialog(a.label,'ASSET DETAIL · 动画详情',`<div class="asset-detail-grid"><div class="asset-detail-preview">${canvas(id,{animate:true,cls:'screen',semanticId:a.object_kind==='listen'?'listen':semantic})}</div><div class="asset-detail-info"><p class="mono muted">${esc(id)}</p><div class="fact-row"><span>来源</span><strong>${a.origin==='official'?'官方资源快照':'E1.45 新候选'}</strong></div><div class="fact-row"><span>原始尺寸</span><strong>${a.frame_width} × ${a.frame_height}</strong></div><div class="fact-row"><span>动画</span><strong>${a.frame_count} 帧 · ${a.fps} FPS</strong></div><div class="fact-row"><span>EAF 大小</span><strong>${kib(a.bytes)}</strong></div><div class="fact-row"><span>当前角色引用</span><strong>${refs.length} 处</strong></div><div class="semantic-tags">${refs.map(([sid])=>`<button data-action="edit-semantic" data-semantic="${sid}">${esc(label(sid))}</button>`).join('')||'<span class="small muted">这个角色暂未使用</span>'}</div>${button('用于当前表情：'+label(semantic),'use-detail-resource','button-primary',`data-resource="${esc(id)}" ${D.semantics[semantic].allowed_resources.includes(id)?'':'disabled'}`)}</div></div>`,'asset');
  }
  function deviceInfo(){showDialog('身体与创作的进度','PROJECT STATUS · 已保存记录',`<div class="device-info-grid"><div><h3>桌宠本体</h3><div class="fact-row"><span>App / bootloader</span><strong>E1.42 / E1.40</strong></div><div class="fact-row"><span>活动资源</span><strong>E1.18 · 8 MiB 分区</strong></div><div class="fact-row"><span>Flash / PSRAM</span><strong>32 MiB / 16 MiB</strong></div><p style="margin-top:12px">这里展示已保存的版本记录。实时设备连接将在后续阶段接入。</p></div><div><h3>现在可以做什么</h3><p>预览动画、查看触发链路、替换草稿接线、编辑角色、导入导出和查看发布差异。</p><p style="margin-top:12px">底座维修期间，表情工作台可以独立使用。新害羞动画已完成离线资源包准备。</p></div></div><div class="progress-list"><div>资源与节点工作台${chip('本轮可用','green')}</div><div>新害羞动画上机${chip('待实机验收','amber')}</div><div>设备双向连接${chip('后续阶段')}</div><div>外壳持久登记${chip('底座恢复后')}</div><div>角色与 DeepSeek${chip('客户端阶段')}</div></div>`,'device');}
  function actionDetail(id){const action=D.actions.find(a=>a.id===id);if(!action)return;const refs=Object.entries(D.semantics).filter(([,s])=>s.action_id===id);showDialog(actionNames[id],'BODY ACTION · 现有动作',`<p class="mono muted">${esc(id)}</p><div class="fact-row"><span>执行模块</span><strong>C61 底座</strong></div><div class="fact-row"><span>协议命令 / 数值</span><strong class="mono">0x${action.wire_command.toString(16).padStart(2,'0')} / ${action.wire_value}</strong></div><div class="fact-row"><span>当前工作台</span><strong>查看动作与关联语义</strong></div><h3 style="margin-top:22px">关联的表情语义</h3><div class="semantic-tags">${refs.map(([sid])=>`<button data-action="edit-semantic" data-semantic="${sid}">${esc(label(sid))}</button>`).join('')||'<span class="small muted">通过工具或独立行为触发</span>'}</div><p class="small muted">后续通过统一表达接口接入动作请求、执行回执和角色组合。</p>`,'action');}
  function closeDialog(){$('studio-dialog').close();dialogKind='';observeCanvases();}
  function download(name,documentValue){const url=URL.createObjectURL(new Blob([JSON.stringify(documentValue,null,2)+'\n'],{type:'application/json'})),link=document.createElement('a');link.href=url;link.download=name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),500);}
  function handoff(){const role=active();download('deskbot-studio-handoff.json',{schema:'deskbot.studio_handoff/v1',edition:'E2a',catalog_id:D.catalog_id,role,changes:editor.changes(),budget:editor.budget(),sources:D.sources,firmware_deployable:false,device_written:false,next_step:'package_review_and_device_preflight'});toast('已导出当前角色的接线、资源预算和版本依据。');}
  function editResource(id){if(editor.connect(semantic,id)){time=0;previewMode='draft';closeDialog();changed(label(semantic)+'已接入'+D.assets[id].label+'，原连接保留在对照节点中。');}}
  document.addEventListener('click',event=>{
    const target=event.target.closest('[data-action],[data-navigate]');if(!target||target.disabled)return;
    try{
      if(target.dataset.navigate){event.preventDefault();navigate(target.dataset.navigate);return;}
      const action=target.dataset.action;
      if(action==='select-semantic')selectSemantic(target.dataset.semantic);
      else if(action==='edit-semantic'){if($('studio-dialog').open)closeDialog();selectSemantic(target.dataset.semantic,true);}
      else if(action==='go-editor')navigate('editor');
      else if(action==='go-roles')navigate('roles');
      else if(action==='play'){playing=!playing;last=0;if(playing)startRaf();else pause();syncPlayback();}
      else if(action==='step'){pause();const a=D.assets[displayedResource()];if(a){time=((currentFrame()+1)%a.frame_count)/timing(a.id,semantic).fps;paintAnimated();}}
      else if(action==='preview-mode'){previewMode=target.dataset.mode;time=0;render();}
      else if(action==='pick-resource')picker();
      else if(action==='choose-resource'||action==='use-detail-resource')editResource(target.dataset.resource);
      else if(action==='asset-detail')assetDetail(target.dataset.resource);
      else if(action==='action-detail')actionDetail(target.dataset.motion);
      else if(action==='close-dialog')closeDialog();
      else if(action==='undo'){editor.undo();time=0;changed('已撤销上一次草稿修改。');}
      else if(action==='redo'){editor.redo();time=0;changed('已重做草稿修改。');}
      else if(action==='restore'){editor.restore(semantic);time=0;changed('当前表情已恢复到设备快照的接线。');}
      else if(action==='reset-graph'){offsets={};document.querySelectorAll('.node').forEach(n=>{if(!reduced.matches)n.animate([{transform:n.style.transform||'none'},{transform:'translate(0,0)'}],{duration:400,easing:'cubic-bezier(.22,1,.36,1)'});n.style.transform='';});wires();}
      else if(action==='filter-library'){libraryFilter=target.dataset.filter;render();}
      else if(action==='select-role'){editor.selectRole(target.dataset.role);save();time=0;render();}
      else if(action==='clone-role'){editor.cloneRole();changed('已复制当前角色，可以给它起一个新名字。');}
      else if(action==='export'){download('deskbot-studio-draft.json',editor.exportDocument());toast('已导出所有角色与表情接线。');}
      else if(action==='import')$('import-file').click();
      else if(action==='handoff')handoff();
      else if(action==='device-info')deviceInfo();
    }catch(error){toast(error.message);}
  });
  $('main-nav').addEventListener('click',event=>{const link=event.target.closest('[data-view]');if(link){event.preventDefault();navigate(link.dataset.view);}});
  document.addEventListener('input',event=>{
    if(event.target.id==='semantic-search'){semanticSearch=event.target.value;$('semantic-list').innerHTML=semanticList();}
    if(event.target.id==='library-search'){librarySearch=event.target.value;$('library-grid').innerHTML=libraryCards();observeCanvases();}
    if(event.target.id==='frame-control'){pause();time=Number(event.target.value)/timing(resource(),semantic).fps;paintAnimated();}
  });
  document.addEventListener('change',event=>{
    try{
      if(event.target.id==='role-picker'){editor.selectRole(event.target.value);time=0;save();render();}
      if(event.target.id==='route-picker'){routeId=event.target.value;const r=D.routes.find(r=>r.id===routeId);if(r.outputs.length)semantic=r.outputs[0].semantic;time=0;offsets={};render();}
      if(event.target.id==='role-name'){editor.rename(event.target.value);save();render();toast('角色名称已保存。');}
    }catch(error){toast(error.message);render();}
  });
  $('import-file').addEventListener('change',async event=>{
    const file=event.target.files[0];if(!file)return;
    try{if(file.size>Core.MAX_BYTES)throw Error('草稿文件应小于 512 KiB');const raw=await file.text();editor.importText(raw);time=0;changed('已导入角色与接线，原草稿可以撤销恢复。');}catch(error){toast('导入未完成：'+error.message);}finally{event.target.value='';}
  });
  document.addEventListener('pointerdown',event=>{
    const handle=event.target.closest('[data-drag-handle]');if(!handle||event.button!==0)return;
    const node=handle.closest('[data-node]'),id=node.dataset.node,offset=offsets[id]||{x:0,y:0};
    drag={id,node,handle,startX:event.clientX,startY:event.clientY,x:offset.x,y:offset.y};handle.setPointerCapture(event.pointerId);node.classList.add('is-dragging');event.preventDefault();
  });
  document.addEventListener('pointermove',event=>{if(!drag)return;const p={x:Math.max(-70,Math.min(70,drag.x+event.clientX-drag.startX)),y:Math.max(-25,Math.min(70,drag.y+event.clientY-drag.startY))};offsets[drag.id]=p;drag.node.style.transform=`translate(${p.x}px,${p.y}px)`;wires();});
  function endDrag(){if(drag)drag.node.classList.remove('is-dragging');drag=null;}
  document.addEventListener('pointerup',endDrag);document.addEventListener('pointercancel',endDrag);
  $('studio-dialog').addEventListener('click',event=>{if(event.target===$('studio-dialog')){const r=$('studio-dialog').getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)closeDialog();}});
  $('studio-dialog').addEventListener('cancel',()=>{dialogKind='';});
  $('studio-dialog').addEventListener('close',()=>{if(dialogKind){dialogKind='';observeCanvases();}});
  document.addEventListener('keydown',event=>{if(event.target.matches('input,textarea,select')||$('studio-dialog').open)return;if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'){event.preventDefault();event.shiftKey?editor.redo():editor.undo();changed('草稿接线已更新。');}});
  document.addEventListener('visibilitychange',()=>{if(document.hidden){cancelAnimationFrame(raf);raf=0;last=0;}else startRaf();});
  window.addEventListener('hashchange',()=>{const next=ownView(location.hash.slice(1));if(next!==view){view=next;render();}});
  window.addEventListener('resize',wires);
  $('theme-toggle').addEventListener('click',()=>{const theme=document.body.dataset.theme==='dark'?'light':'dark';document.body.dataset.theme=theme;try{localStorage.setItem('deskbot.studio.theme',theme);}catch(error){/* Theme remains available for this page. */}});
  try{document.body.dataset.theme=localStorage.getItem('deskbot.studio.theme')||'light';}catch(error){document.body.dataset.theme='light';}
  mountIcons();render();if(initial!==D.initial)$('save-state').innerHTML='<span></span>已恢复草稿';if(recoveryMessage)toast(recoveryMessage);
  // Expose read-only snapshots for browser acceptance checks and debugging.
  Object.defineProperty(window,'DeskBotStudio',{value:Object.freeze({snapshot:()=>editor.snapshot(),budget:()=>editor.budget(),
    state:()=>({view,semantic,routeId,playing,time,media_scope:D.media_scope,device_live:false}),catalog:()=>({semantics:Object.keys(D.semantics).length,assets:Object.keys(D.assets).length})})});
})();
