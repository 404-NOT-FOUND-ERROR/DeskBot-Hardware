  // These controls share the existing click/readiness/single-owner request path.
  const expressionOperations=['neutral','embarrassed','eat','happy','sad','sleepy','confused','surprised'];
  const commonExpressions=[
    {id:'happy',label:'开心',action:'点头'},
    {id:'sad',label:'难过',action:'轻点头'},
    {id:'sleepy',label:'困倦',action:'轻点头 · 表情与系统睡眠分别控制'},
    {id:'confused',label:'困惑',action:'张望'},
    {id:'surprised',label:'惊讶',action:'张望'}
  ];
  function commonExpressionView(){
    return `<p id="e3f7-capabilities" class="e3c-reason">连接后显示设备开放的表情。</p>${commonExpressions.map(row=>`<div class="e3c-command"><div><b>${row.label} · ${row.id}</b><span id="e3f7-${row.id}-binding">读取目录后显示当前绑定</span><small>双眼镜像 · 原有附带动作：${row.action}</small></div><button class="button button-primary" data-e3c-action="${row.id}" disabled>播放${row.label}</button></div>`).join('')}`;
  }
  function paintCommonExpressions({slot,d,semantics,sameDirectory,directory,occupied,usable,preparing,text}){
    text('e3f7-capabilities',d?`设备已开放 ${semantics.length} 种表情 · 按实际能力启用按钮`:'连接后显示设备开放的表情。');
    for(const row of commonExpressions){
      const binding=sameDirectory?directory.manifest.entries.find(r=>r.kind==='expression'&&r.id===row.id):null;
      text(`e3f7-${row.id}-binding`,binding?`目录绑定 ${binding.resource_id} · ${binding.fps} fps · ${binding.loop?'循环':'单次'}`:'读取当前设备目录后显示绑定');
      const button=slot.querySelector(`[data-e3c-action="${row.id}"]`);
      if(!button)continue;
      button.disabled=occupied||!usable||!binding||!semantics.includes(row.id);
      button.textContent=preparing?'核对后播放…':`播放${row.label}`;
      button.title=!d?'启用状态查询后可核对':!semantics.includes(row.id)?'当前设备尚未开放这个入口':!binding?'正在准备设备目录':`点击后核对最新状态，再播放${row.label}`;
    }
  }
