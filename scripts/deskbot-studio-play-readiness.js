/* A click waits for a fresh reply before using the existing one-shot command. */
((root)=>{
  'use strict';
  const keys=['connection_id','target_id','boot_id','session_id'];
  function sessionKey(value){
    return value?.enabled&&keys.every(key=>typeof value[key]==='string'&&value[key])?keys.map(key=>value[key]).join('|'):null;
  }
  function entry(value,semantic){
    const directory=value?.catalog;
    if(!sessionKey(value)||directory?.status!=='complete'||!directory.manifest||
       !keys.slice(1).every(key=>directory[key]===value[key]))return null;
    return directory.manifest.entries.find(row=>row.kind==='expression'&&row.id===semantic)||null;
  }
  function available(display){
    return Boolean(display&&display.application?.phase==='settled'&&display.application.value==='idle'&&
      display.page?.phase==='settled'&&display.page.value==='home'&&display.player?.state==='initialized');
  }
  async function play({operation,initial,read,send,observe=()=>{},isCurrent=()=>true,
      now=()=>performance.now(),wait=ms=>new Promise(resolve=>setTimeout(resolve,ms)),timeout=12000}){
    const key=sessionKey(initial),binding=entry(initial,operation),started=now();
    if(!key||(operation!=='neutral'&&!binding))throw Error('先读取当前设备目录，再播放表情。');
    const catalog=initial.catalog?.manifest?.catalog_id;
    while(now()-started<timeout){
      if(!isCurrent())throw Error('本次播放已取消，请查看当前连接。');
      const sent=now(),next=await read(Math.min(4000,timeout-(now()-started))),rtt=now()-sent;
      if(!isCurrent())throw Error('本次播放已取消，请查看当前连接。');
      if(sessionKey(next)!==key)throw Error('设备或会话已变化，请重新选择播放。');
      observe(next);
      // Recheck the same installed binding; cached display facts never authorize a write.
      if(binding&&(!entry(next,operation)||next.catalog.manifest.catalog_id!==catalog||
          JSON.stringify(entry(next,operation))!==JSON.stringify(binding)))throw Error('设备目录已变化，请重新核对表情。');
      if(next.display){
        if(!available(next.display))throw Error('桌宠正在对话或切换页面，回主页待机后再播放。');
        if(!(next.display.expression_semantics??['neutral']).includes(operation))throw Error('当前固件尚未接入这个表情。');
      }
      if(now()-started>=timeout)break;
      // Leave scheduling room before the next query (1 s after a reply).
      if(next.can_submit&&next.display&&Number.isInteger(next.age_ms)&&next.age_ms>=0&&next.age_ms+rtt<350){
        return await send(next); // One POST only, including HTTP errors and late acknowledgements.
      }
      await wait(Math.min(150,timeout-(now()-started)));
    }
    throw Error('本次查询尚未就绪，请稍后再点播放。');
  }
  const api=Object.freeze({sessionKey,entry,available,play});
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else root.DeskBotStudioPlayReadiness=api;
})(typeof window==='undefined'?globalThis:window);
