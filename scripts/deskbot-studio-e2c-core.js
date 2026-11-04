/* Transport-independent expression contract and deterministic computer simulator. */
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.DeskBotStudioE2CCore=api;})(typeof globalThis==='object'?globalThis:this,function(){
  'use strict';
  const SCHEMA='deskbot.expression_control/v1',TARGET='deskbot-studio-simulator';
  const ID=/^[A-Za-z][A-Za-z0-9_.:-]{0,95}$/,HASH=/^[a-f0-9]{64}$/;
  const identifier=v=>typeof v==='string'&&ID.test(v),digest=v=>typeof v==='string'&&HASH.test(v);
  const LIMITS=Object.freeze({max_inflight:1,max_history:24,max_dedup:64,default_timeout_ms:3000});
  const clone=v=>JSON.parse(JSON.stringify(v));
  const canonical=v=>JSON.stringify(normalize(v));
  function normalize(v){return Array.isArray(v)?v.map(normalize):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,normalize(v[k])])):v;}
  function keys(v,expected){return !!v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join('|')===[...expected].sort().join('|');}
  function integer(v,min,max=Number.MAX_SAFE_INTEGER){return Number.isSafeInteger(v)&&v>=min&&v<=max;}
  function validTime(v){return typeof v==='string'&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(v)&&Number.isFinite(Date.parse(v))&&new Date(v).toISOString()===v;}
  function validateCatalog(c){
    if(!keys(c,['schema','catalog_id','library_catalog_id','revision','role_id','source','live','device','expressions','actions','limits'])||c.schema!=='deskbot.expression_catalog/v1'||!digest(c.catalog_id)||!digest(c.library_catalog_id)||c.revision!=='E1.18'||!identifier(c.role_id)||c.source!=='saved_release_record'||c.live!==false||!keys(c.device,['source','live','app','bootloader','assets','layout','flash_bytes','psram_bytes'])||c.device.source!=='saved_release_record'||c.device.live!==false||!identifier(c.device.app)||!identifier(c.device.bootloader)||c.device.assets!=='E1.18'||c.device.layout!=='upper-assets-8m'||c.device.flash_bytes!==33554432||c.device.psram_bytes!==16777216)throw Error('invalid saved expression catalog');
    if(!Array.isArray(c.expressions)||!c.expressions.length||c.expressions.length>128||!Array.isArray(c.actions)||c.actions.length>32||canonical(c.limits)!==canonical(LIMITS))throw Error('invalid catalog bounds');
    const actions=new Set(),wire=new Set(),semantics=new Set();
    for(const a of c.actions){const pair=a.wire_command+':'+a.wire_value;
      if(!keys(a,['action_id','label','wire_command','wire_value','completion'])||!identifier(a.action_id)||actions.has(a.action_id)||typeof a.label!=='string'||!a.label||a.wire_command!==2||!integer(a.wire_value,0,4)||wire.has(pair)||a.completion!=='fact_only')throw Error('invalid action catalog entry');
      actions.add(a.action_id);wire.add(pair);
    }
    for(const e of c.expressions){
      if(!keys(e,['semantic_id','label','resource_id','layout_id','fps','loop','requestable','implicit_action_id'])||!identifier(e.semantic_id)||semantics.has(e.semantic_id)||typeof e.label!=='string'||!e.label||typeof e.resource_id!=='string'||!/^[A-Za-z0-9_]+\.eaf$/.test(e.resource_id)||!['eye_anim','listen_anim'].includes(e.layout_id)||!integer(e.fps,1,60)||typeof e.loop!=='boolean'||typeof e.requestable!=='boolean'||(e.implicit_action_id!==null&&!actions.has(e.implicit_action_id)))throw Error('invalid expression catalog entry');
      if((e.semantic_id==='listen')!==(e.layout_id==='listen_anim')||e.requestable===(e.semantic_id==='listen'))throw Error('listen belongs to the voice state');
      semantics.add(e.semantic_id);
    }
    return c;
  }
  async function verifyCatalog(c){
    validateCatalog(c);
    const unsigned=Object.fromEntries(Object.entries(c).filter(([key])=>key!=='catalog_id'));
    const raw=await globalThis.crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(unsigned)));
    const actual=Array.from(new Uint8Array(raw),b=>b.toString(16).padStart(2,'0')).join('');
    if(actual!==c.catalog_id)throw Error('expression catalog digest differs from its contents');
    return c;
  }
  function validateRequest(r,c){
    if(!keys(r,['schema','message_type','request_id','target_id','boot_id','session_id','sequence','catalog_id','role_id','issued_at','expires_after_ms','command'])||r.schema!==SCHEMA||r.message_type!=='request'||!identifier(r.request_id)||!identifier(r.target_id)||!identifier(r.boot_id)||!identifier(r.session_id)||!integer(r.sequence,1)||!digest(r.catalog_id)||!identifier(r.role_id)||!validTime(r.issued_at)||!integer(r.expires_after_ms,100,10000))throw Error('invalid expression request envelope');
    if(c&&(r.catalog_id!==c.catalog_id||r.role_id!==c.role_id))throw Error('request catalog or role is stale');
    const op=r.command;
    if(keys(op,['kind','semantic_id'])&&op.kind==='play_expression'&&identifier(op.semantic_id)){
      if(c&&!c.expressions.some(e=>e.semantic_id===op.semantic_id&&e.requestable))throw Error('expression is unavailable or owned by the voice state');
    }else if(keys(op,['kind','action_id'])&&op.kind==='play_action'&&identifier(op.action_id)){
      if(c&&!c.actions.some(a=>a.action_id===op.action_id))throw Error('action is unavailable');
    }else throw Error('unsupported expression command');
    return r;
  }
  function create(directory,{clock=()=>performance.now(),wallClock=()=>new Date().toISOString(),dispatchDelay=160}={}){
    const catalog=clone(validateCatalog(directory));
    if(!integer(dispatchDelay,0,10000))throw Error('invalid simulator dispatch delay');
    let connected=false,mode='normal',boot=1,session=0,requestSequence=0,eventSequence=0,highWatermark=0;
    let pending=null,journal=[],visible=[],notices=[],preview='idle',lastTime=-1;
    let ledger=new Map(),executionCount=0,duplicateCount=0;
    const bootId=()=>'sim-boot-'+boot,sessionId=()=>'sim-session-'+session;
    const time=()=>{const n=clock();if(!Number.isFinite(n)||n<lastTime)throw Error('simulator clock must be monotonic');lastTime=n;return n;};
    function emit(request,status,reason,{remember=true,original_status=null,at=time()}={}){
      const event={schema:SCHEMA,message_type:'receipt',target_id:TARGET,boot_id:bootId(),session_id:sessionId(),sequence:++eventSequence,request_id:request.request_id,request_sequence:request.sequence,status,reason,source:'simulator',correlated:true,original_status,at_ms:Math.floor(at)};
      journal.push(event);journal=journal.slice(-LIMITS.max_history);
      if(connected){visible.push(event);visible=visible.slice(-LIMITS.max_history);}
      if(remember&&ledger.has(request.request_id))ledger.get(request.request_id).receipt=clone(event);
      return clone(event);
    }
    function advance(){
      const now=time();if(!pending)return [];
      const request=pending.request;
      if(now>=pending.deadline){pending=null;return [emit(request,'expired','deadline_before_dispatch',{at:now})];}
      if(mode==='stall')return [];
      if(mode==='busy'){pending=null;return [emit(request,'interrupted','owner_changed_before_dispatch',{at:now})];}
      if(mode==='base_offline'&&pending.needsBase){pending=null;return [emit(request,'interrupted','base_disconnected_before_dispatch',{at:now})];}
      if(now<pending.dispatchAt)return [];
      pending=null;executionCount++;
      if(request.command.kind==='play_expression')preview=request.command.semantic_id;
      return [emit(request,'dispatched',request.command.kind==='play_expression'?'submitted_to_display':'submitted_to_base',{at:now})];
    }
    function submit(input){
      const request=clone(validateRequest(input));advance();
      if(!connected)return {delivered:false,local_status:'connection_lost',receipt:null};
      const reject=reason=>({delivered:true,receipt:emit(request,'rejected',reason,{remember:false})});
      if(request.target_id!==TARGET)return reject('target_mismatch');
      if(request.boot_id!==bootId())return reject('boot_changed');
      const fingerprint=canonical(request),previous=ledger.get(request.request_id);
      if(previous){
        if(previous.fingerprint!==fingerprint)return reject('request_id_conflict');
        duplicateCount++;return {delivered:true,receipt:emit(request,'duplicate','replayed_receipt',{remember:false,original_status:previous.receipt.status})};
      }
      if(request.session_id!==sessionId())return reject('session_changed');
      if(request.catalog_id!==catalog.catalog_id||request.role_id!==catalog.role_id)return reject('catalog_or_role_changed');
      if(request.sequence<=highWatermark)return reject('sequence_not_increasing');
      if(ledger.size>=LIMITS.max_dedup)return reject('idempotency_window_full');
      highWatermark=request.sequence;ledger.set(request.request_id,{fingerprint,request:clone(request),receipt:null});
      try{validateRequest(request,catalog);}catch(error){return {delivered:true,receipt:emit(request,'rejected','unsupported_expression_or_action')};}
      const needsBase=request.command.kind==='play_action';
      if(pending||mode==='busy')return {delivered:true,receipt:emit(request,'busy','existing_owner_or_request')};
      if(mode==='base_offline'&&needsBase)return {delivered:true,receipt:emit(request,'rejected','base_unavailable')};
      const now=time();pending={request:clone(request),deadline:now+request.expires_after_ms,dispatchAt:now+dispatchDelay,needsBase};
      return {delivered:true,receipt:emit(request,'accepted','queued_for_owner',{at:now})};
    }
    function makeRequest(command,{expiresAfterMs=LIMITS.default_timeout_ms}={}){
      requestSequence++;
      return clone(validateRequest({schema:SCHEMA,message_type:'request',request_id:'sim-r'+boot+'-'+String(requestSequence).padStart(5,'0'),target_id:TARGET,boot_id:bootId(),session_id:sessionId(),sequence:requestSequence,catalog_id:catalog.catalog_id,role_id:catalog.role_id,issued_at:wallClock(),expires_after_ms:expiresAfterMs,command:clone(command)}));
    }
    function connect(){if(!connected){session++;connected=true;visible=clone(journal);}return state();}
    function disconnect(){advance();connected=false;return state();}
    function setMode(value){if(!['normal','busy','stall','base_offline'].includes(value))throw Error('unsupported simulator mode');advance();mode=value;advance();return state();}
    function baseFact(){
      const event={schema:SCHEMA,message_type:'base_fact',target_id:TARGET,boot_id:bootId(),session_id:sessionId(),sequence:++eventSequence,fact:'base.action.completed',request_id:null,correlated:false,source:'simulator',at_ms:Math.floor(time())};
      journal.push(event);journal=journal.slice(-LIMITS.max_history);if(connected){visible.push(event);visible=visible.slice(-LIMITS.max_history);}return clone(event);
    }
    function clientTimeout(requestId){
      if(!ledger.has(requestId))throw Error('unknown request');
      const notice={request_id:requestId,status:'timeout',reason:'result_unknown_until_snapshot',source:'client',physical_result:'unknown'};
      notices.push(notice);notices=notices.slice(-LIMITS.max_history);return clone(notice);
    }
    function restart(){
      boot++;session++;connected=false;pending=null;ledger=new Map();journal=[];visible=[];notices=[];highWatermark=0;requestSequence=0;eventSequence=0;preview='idle';mode='normal';return state();
    }
    function state(){return clone({target_id:TARGET,boot_id:bootId(),session_id:sessionId(),catalog_id:catalog.catalog_id,connected,mode,preview_semantic:preview,pending:pending?{request_id:pending.request.request_id,deadline_ms:pending.deadline}:null,receipts:visible,client_notices:notices,recent_results:[...ledger.values()].filter(v=>v.receipt).slice(-LIMITS.max_history).map(v=>({request:v.request,receipt:v.receipt})),execution_count:executionCount,duplicate_count:duplicateCount,retained_decisions:ledger.size,limits:LIMITS,hardware_accessed:false,device_live:false});}
    return Object.freeze({catalog:()=>clone(catalog),makeRequest,submit,advance,connect,disconnect,setMode,baseFact,clientTimeout,restart,state});
  }
  return Object.freeze({SCHEMA,TARGET,LIMITS,canonical,validateCatalog,verifyCatalog,validateRequest,create});
});
