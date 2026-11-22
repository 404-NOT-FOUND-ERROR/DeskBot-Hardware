/* Capacity is a reviewed packaging projection, separate from live device status. */
(function(root){
  'use strict';
  const mib=bytes=>(bytes/1048576).toFixed(2),kib=bytes=>(bytes/1024).toFixed(1);
  function create(plan){
    function view(){
      return `<section class="role-pack-plan" aria-label="角色包与容量规划">
      <div class="pack-heading"><div><span class="eyebrow">LIBRARY → ROLE PACK → DESKBOT</span><h2>素材在电脑，常用角色随身带。</h2><p>电脑保存完整素材库，桌宠按容量常驻一到两套常用角色。新角色首次同步，已安装角色随换壳切换。</p></div><span class="pack-badge">角色包方案</span></div>
      <div class="pack-journey" aria-label="角色资源使用流程"><b>电脑素材库</b><i>→</i><b>精选角色包</b><i>→</i><b>同步到桌宠</b><i>→</i><b>换壳即切换</b></div>
      <div class="pack-stats"><div><small>E1.45 留存资源包</small><strong>${mib(plan.base_bytes)}<span>MiB</span></strong></div><div><small>圆团 · 六种三段表情</small><strong>${mib(plan.added_eaf_bytes)}<span>MiB · 去重后</span></strong></div><div><small>双角色容量预估</small><strong>${mib(plan.projected_bytes)}<span>/ ${plan.partition_bytes/1048576} MiB</span></strong></div></div>
      <div class="pack-meter" role="meter" aria-label="双角色资源容量预估" aria-valuenow="${plan.projected_bytes}" aria-valuemin="0" aria-valuemax="${plan.partition_bytes}" aria-valuetext="双角色容量预估 ${mib(plan.projected_bytes)} MiB，分区 ${plan.partition_bytes/1048576} MiB"><span class="pack-meter-base" style="width:${plan.base_bytes/plan.partition_bytes*100}%"></span><span class="pack-meter-added" style="width:${(plan.projected_bytes-plan.base_bytes)/plan.partition_bytes*100}%"></span><i class="pack-meter-mark" style="left:${plan.soft_budget_bytes/plan.partition_bytes*100}%" aria-hidden="true"></i></div>
      <div class="pack-meter-key"><span><i></i>原有资源 <i class="added"></i>圆团片段与索引</span><span>${plan.soft_budget_bytes/1048576} MiB 建议线 · ${plan.partition_bytes/1048576} MiB 分区</span></div>
      <p class="pack-footnote">预估已含平面索引，正式角色图与布局接入后核算最终包。<b>距建议线约 ${kib(plan.soft_budget_bytes-plan.projected_bytes)} KiB</b>。原包含字体、模型与系统素材；圆团完整素材库约 ${mib(plan.full_library_bytes)} MiB，按角色选用。</p>
      <details class="pack-details"><summary>首次同步与空间管理</summary><div class="pack-scenarios"><div><h3>第一次使用新外壳</h3><p>确认绑定 → 预览角色 → 安装资源包 → 回读通过 → 启用设备角色。电脑预览选择与设备状态分别显示。</p></div><div><h3>空间快用满时</h3><p>保留日常必备表情；可选动画按需精简，或替换不常用角色。先看容量与变更，再同步；外壳绑定、人格与记忆继续保留。</p></div><div><h3>电脑暂时不在身边</h3><p>已安装角色直接切换。新角色显示“已识别，角色待同步”，继续使用当前角色，方便时连接电脑安装。</p></div></div></details></section>`;
    }
    return Object.freeze({view});
  }
  root.DeskBotRolePacks=Object.freeze({create});
})(globalThis);
