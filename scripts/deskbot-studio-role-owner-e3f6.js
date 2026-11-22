    // This entry shares busy/generation with the live buttons and query owner.
    async function playRole({operation,isCurrent,resolve}){
      if(closed||busy||!value||value.host_command||value.pending)throw Error('上一项请求处理中，稍后再播放。');
      const initial=value,initialKey=activeKey(),version=++generation;
      if(!initialKey)throw Error('连接 USB 并启用状态查询后播放。');
      busy=true;preparing=true;actionError='';paint();
      const current=()=>!closed&&generation===version&&activeKey()===initialKey&&isCurrent();
      try{
        const next=await readiness.play({operation,initial,read:budget=>api('/api/studio/state',null,budget),isCurrent:current,
          observe:state=>{accept(state);paint();},send:async state=>{
            const plan=await resolve();
            // Analysis is read-only. Recheck the click after its HTTP reply too.
            if(!current()||plan.catalog_id!==state.catalog?.manifest?.catalog_id||
                ['connection_id','target_id','boot_id','session_id'].some(k=>plan.command?.[k]!==state[k]))throw Error('接线或设备已变化，已结束本次等待。');
            preparing=false;paint();
            return api('/api/studio/command',plan.command);
          }});
        accept(next);error='';return next;
      }catch(e){actionError=e.message;throw e;}
      finally{busy=false;preparing=false;paint();if(!closed)void refresh();}
    }
