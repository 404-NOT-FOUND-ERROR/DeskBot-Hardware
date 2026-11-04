(function(){
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const size=v=>v>0?`${(v/1048576).toFixed(0)} MiB`:'待确认';
  const titles={confirmed:'身份已收到',stale:'等待更新',waiting:'等待设备自报',disconnected:'等待 USB 连接'};
  function create(){
    function decorate(html,state){
      const d=state.device,i=d?.identity;
      const connected=d?.connection?.status==='connected';
      const h=connected?i?.hello:null,ready=connected&&i?.status==='confirmed';
      const status=connected?(i?.status||'waiting'):'disconnected';
      const fields=h?`<dl class="e3b-info"><div><dt>设备</dt><dd title="${esc(h.device_id)}">喵伴 · ${esc(h.device_id.slice(-8))}</dd></div><div><dt>固件</dt><dd>${esc(h.firmware.edition)} · ${esc(h.firmware.version)}</dd></div><div><dt>存储 / 内存</dt><dd>${size(h.hardware.flash_bytes)} / ${size(h.hardware.psram_bytes)}</dd></div><div><dt>本次启动</dt><dd>${esc(h.boot_id.slice(-8))} · #${esc(h.sequence)}</dd></div></dl>`:
        `<p class="e3b-wait">${connected?'串口已连接，等待固件发送身份、版本与能力。已保存的 E1.42 版本记录尚未提供身份消息。':'连接桌宠头部后，这里显示设备自报的身份、版本与能力。'}</p>`;
      const identity=`<section class="surface e3b-handshake" aria-label="设备身份与连接进度"><div class="e3b-title"><div><div class="eyebrow">DEVICE LINK · E3b</div><h2>认识连接的桌宠</h2></div><span class="e3b-status ${ready?'is-ready':''}" role="status">${esc(titles[status]||titles.waiting)}</span></div><ol class="e3b-chain"><li class="${connected?'done':''}"><b>01</b><span>USB 连接</span></li><li class="${ready?'done':''}"><b>02</b><span>身份与版本</span></li><li><b>03</b><span>表情目录</span><small>下一阶段</small></li><li><b>04</b><span>表达请求</span><small>后续接入</small></li></ol>${fields}<p class="e3b-caption">${h?`来源：设备自报 · App ${esc(h.firmware.app_elf_sha256.slice(0,12))} · ${status==='stale'?'上次收到的版本，等待设备更新':'只读身份接口'}`:'安装 E3b 固件后，在这里验收设备身份与版本。'}</p><p class="e3b-caption">当前表情预览使用已保存的 E1.18 目录。</p></section>`;
      return identity+html.replace('读取已有 USB 控制台日志，不发送命令或改变设备状态。','通过 USB 读取设备消息和已有状态日志。');
    }
    return DeskBotStudioE3AUI.create({decorate});
  }
  window.DeskBotStudioE3BUI=Object.freeze({create});
})();
