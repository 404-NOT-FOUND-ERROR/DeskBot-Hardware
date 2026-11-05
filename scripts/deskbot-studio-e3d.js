(function(){
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function create(data,host){
    const directory=data.installed_directory,manifest=directory.manifest;
    const entries=manifest.entries.filter(r=>r.kind==='expression'||r.kind==='status');
    const actions=new Map(data.actions.map(r=>[r.id,r.label]));
    const labels=id=>data.semantics[id]?.label||id;
    let selected='surprised',filter='all',search='';
    function rows(){
      const q=search.trim().toLowerCase();
      return entries.filter(r=>(filter==='all'||filter==='motion'&&r.implicit_action_id||filter==='visual'&&r.kind==='expression'&&!r.implicit_action_id||filter==='status'&&r.kind==='status')
        &&(r.id+' '+labels(r.id)+' '+r.resource_id+' '+(actions.get(r.implicit_action_id)||'')).toLowerCase().includes(q));
    }
    function renderRow(r){return `<button class="e3d-row ${selected===r.id?'selected':''}" data-e3d-select="${esc(r.id)}" aria-pressed="${selected===r.id}"><span><b>${esc(labels(r.id))}</b><small>${esc(r.id)}</small></span><span class="e3d-resource">${esc(r.resource_id)}</span><span class="e3d-row-note">${r.kind==='status'?'语音状态':r.implicit_action_id?esc(actions.get(r.implicit_action_id)):'纯表情'}</span></button>`;}
    function preview(){
      const row=entries.find(r=>r.id===selected),layout=manifest.entries.find(r=>r.kind==='layout'&&r.id===row.layout_id);
      const draft=host.role(),draftResource=draft.bindings[row.id],changed=draftResource!==row.resource_id;
      return `<article class="e3d-preview"><div class="e3d-preview-heading"><div><h3>${esc(labels(row.id))}</h3><span>${esc(row.id)}</span></div><span class="e3d-tag">${row.kind==='status'?'语音状态专用':directory.usb.supported_expression_ids.includes(row.id)?'USB 已接入 · 待实测':'USB 待接入'}</span></div><div class="e3d-comparison"><div><span class="e3d-overline">已核对资源</span>${host.canvas(row.resource_id,{animate:true,cls:'screen',semanticId:row.id})}<b>${esc(row.resource_id)}</b></div><div><span class="e3d-overline">角色草稿 · ${esc(draft.name)}</span>${host.canvas(draftResource,{animate:true,cls:'screen',semanticId:row.id})}<b>${esc(draftResource||'未连接')}</b><small>${changed?'草稿已改接，等待发布':'与资源快照一致'}</small></div></div><div class="e3d-link"><span>${esc(labels(row.id))}</span><i></i><span>${esc(row.resource_id)}</span><i></i><span>${row.layout_id==='eye_anim'?'双眼布局':'聆听图标'}</span></div><dl class="e3d-properties"><div><dt>播放</dt><dd>${row.fps} FPS · ${row.loop?'循环':'单次'}</dd></div><div><dt>布局</dt><dd>${layout.mirror==='auto'?'自动镜像':'独立图像'} · 偏移 ${layout.x}, ${layout.y}</dd></div><div><dt>附带动作</dt><dd>${esc(actions.get(row.implicit_action_id)||'无')}</dd></div><div><dt>资源大小</dt><dd>${(row.bytes/1024).toFixed(1)} KiB</dd></div></dl><button class="button" data-action="edit-semantic" data-semantic="${esc(row.id)}">在节点中查看连接</button><details class="e3d-digest"><summary>资源指纹</summary><code>${esc(row.resource_sha256)}</code></details></article>`;
    }
    function view(){return `<section class="surface e3d-directory" aria-label="已核对表情目录"><div class="e3d-heading"><div><div class="eyebrow">EXPRESSION DIRECTORY · E3d-1</div><h2>知道它装了什么。</h2><p>查看表情、圆屏效果、附带动作与角色草稿。</p></div><div class="e3d-badges"><span class="e3d-tag">E1.18 · 已验证资源快照</span><span class="e3d-tag muted">设备实时目录 · 待接入</span></div></div><div class="e3d-stats"><div><strong>${directory.audit.expression_count}</strong><span>表情语义</span></div><div><strong>${directory.audit.unique_eaf_count}</strong><span>EAF 文件</span></div><div><strong>${directory.audit.expressions_with_actions}</strong><span>表情附带动作</span></div><div><strong>${directory.audit.status_animation_count}</strong><span>聆听状态动画</span></div></div><div class="e3d-source-note"><b>当前版本的“惊讶” → shocked.eaf</b><span>源码默认配置为 Happy.eaf；这里按已验收的资源包展示。</span></div><div class="e3d-content"><div class="e3d-list-panel"><label class="e3d-search"><span>查找表情</span><input id="e3d-search" type="search" value="${esc(search)}" placeholder="名称、文件或动作" autocomplete="off"></label><div class="e3d-filters" aria-label="目录分类">${[['all','全部'],['motion','附带动作'],['visual','纯表情'],['status','聆听状态']].map(([id,text])=>`<button data-e3d-filter="${id}" aria-pressed="${filter===id}">${text}</button>`).join('')}</div><p class="e3d-count">${rows().length} 个条目 · 点击查看</p><div class="e3d-rows">${rows().map(renderRow).join('')||'<p class="e3d-empty">没有找到匹配的表情</p>'}</div></div>${preview()}</div><details class="e3d-contract"><summary>下一步：设备返回真实目录</summary><p>沿用同一 USB 连接，按页读取表情与动作清单。电脑收齐并核对版本后，再更新“设备已安装”状态；资源目录与可请求能力分别显示。</p><ol><li>设备读取已挂载资源与原动作映射</li><li>USB 分页返回，携带设备、启动和会话编号</li><li>工作台收齐清单，核对完整摘要</li><li>逐步开放已安装表情，记录执行回执</li></ol></details></section>`;}
    function repaint(focus=false){const slot=document.getElementById('installed-directory-slot');if(!slot)return;slot.innerHTML=view();host.paint();if(focus){const input=document.getElementById('e3d-search');input.focus();}}
    function init(){
      document.addEventListener('click',event=>{const choose=event.target.closest('[data-e3d-select]'),group=event.target.closest('[data-e3d-filter]');if(choose){selected=choose.dataset.e3dSelect;repaint();}else if(group){filter=group.dataset.e3dFilter;repaint();}});
      document.addEventListener('input',event=>{if(event.target.id==='e3d-search'){search=event.target.value;const list=document.querySelector('.e3d-rows'),count=document.querySelector('.e3d-count');if(list)list.innerHTML=rows().map(renderRow).join('')||'<p class="e3d-empty">没有找到匹配的表情</p>';if(count)count.textContent=rows().length+' 个条目 · 点击查看';}});
    }
    return Object.freeze({view,init,state:()=>({source:directory.source,live:false,manifest_id:manifest.catalog_id,selected,filter,visible:rows().length,usb:JSON.parse(JSON.stringify(directory.usb))})});
  }
  window.DeskBotStudioE3DUI=Object.freeze({create});
})();
