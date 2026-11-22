/* Visual effects share no state or timers with expression playback and device commands. */
(function(root){
  'use strict';
  const reduced=matchMedia('(prefers-reduced-motion: reduce)'),animations=new Set(),folds=new Map();
  const ease='cubic-bezier(.16,1,.3,1)',navEase='cubic-bezier(.22,1,.36,1)';
  let ready=false,lastRoute=null,nav=null,indicator=null,navAnimation=null,arrivalObserver=null,routeSequence=0,entrances=0;
  function play(element,frames,options){
    if(reduced.matches||document.hidden||!element?.isConnected)return null;
    const animation=element.animate(frames,options);animations.add(animation);
    animation.finished.then(()=>animations.delete(animation),()=>animations.delete(animation));
    return animation;
  }
  function cancelArrivals(){
    arrivalObserver?.disconnect();arrivalObserver=null;
    // Replaced DOM is already detached here; track ownership instead of ancestry.
    for(const a of animations)if(a!==navAnimation&&!folds.has(a.effect?.target))a.cancel();
  }
  function updateNavigation(animate=true){
    if(!nav||!indicator)return;
    const active=nav.querySelector('a[aria-current=page]');
    if(!active){indicator.hidden=true;return;}indicator.hidden=false;
    const previous=indicator.getBoundingClientRect(),base=nav.getBoundingClientRect(),target=active.getBoundingClientRect();
    const x=target.left-base.left+nav.scrollLeft,y=target.top-base.top+nav.scrollTop;
    navAnimation?.cancel();
    Object.assign(indicator.style,{width:target.width+'px',height:target.height+'px',transform:`translate(${x}px,${y}px)`});
    if(animate&&previous.width&&previous.height)navAnimation=play(indicator,[
      {width:previous.width+'px',height:previous.height+'px',transform:`translate(${previous.left-base.left+nav.scrollLeft}px,${previous.top-base.top+nav.scrollTop}px)`},
      {width:target.width+'px',height:target.height+'px',transform:`translate(${x}px,${y}px)`}
    ],{duration:480,easing:navEase});
  }
  function arrive(container){
    if(reduced.matches||!container)return;
    entrances++;
    const page=container.querySelector('#view-content')||container.firstElementChild||container;
    play(page,[{opacity:.25,transform:'translateY(12px)'},{opacity:1,transform:'translateY(0)'}],{duration:520,easing:ease});
    const selector='.page-heading,.runtime-heading,.characters-heading,.sc-heading,.shell-head,.workflow-rail,.overview-grid>*,.runtime-choices,.runtime-toolbar,.runtime-stage,.role-package-editor,.characters-workspace>*,.sc-workspace>*,#shell-runtime-root,.sc-rehearsal,.shell-toolbar,.shell-create,.shell-project,.shell-binding,.asset-grid,.editor-layout,.e3c-page>div,.role-pack-plan';
    const candidates=[...container.querySelectorAll(selector)].filter(el=>{const r=el.getBoundingClientRect();return r.width&&r.height&&r.top<innerHeight&&r.bottom>0;});
    candidates.filter(el=>!candidates.some(parent=>parent!==el&&parent.contains(el))).slice(0,9).forEach((el,i)=>{
      play(el,[{opacity:.3,transform:'translateY(15px) scale(.992)'},{opacity:1,transform:'translateY(0) scale(1)'}],{duration:580,delay:Math.min(i,6)*30,easing:ease});
    });
  }
  function afterRender(container,route){
    init();container.dataset.route=route;
    updateNavigation(lastRoute!==null&&route!==lastRoute);
    for(const [details,record]of folds)if(!details.isConnected){record.animation?.cancel();finishFold(details,record);}
    for(const a of animations)if(!a.effect?.target?.isConnected)a.cancel();
    if(route===lastRoute)return;
    cancelArrivals();lastRoute=route;const sequence=++routeSequence;
    window.scrollTo({top:0,behavior:'instant'});
    // These pages fill asynchronously. Observe only their first content, then disconnect.
    const heading=()=>container.querySelector('.characters-heading,.sc-heading,.shell-head');
    if(['characters','shells','capture'].includes(route)&&!heading()){
      arrivalObserver=new MutationObserver(()=>{if(sequence===routeSequence&&heading()){arrivalObserver.disconnect();arrivalObserver=null;arrive(container);}});
      arrivalObserver.observe(container,{childList:true,subtree:true});
    }else arrive(container);
  }
  function finishFold(details,record){
    if(folds.get(details)!==record)return;
    details.open=record.desired;
    details.style.height=record.height;details.style.overflow=record.overflow;
    delete details.dataset.wbOpen;
    record.children.forEach(([el,inert])=>{el.inert=inert;});folds.delete(details);
  }
  function toggleDetails(details){
    const summary=details.querySelector(':scope>summary'),old=folds.get(details);
    if(!summary)return;
    const desired=!(old?old.desired:details.open),start=details.getBoundingClientRect().height;
    old?.animation?.cancel();
    const record={desired,height:old?.height??details.style.height,overflow:old?.overflow??details.style.overflow,
      children:old?.children??[...details.children].filter(el=>el!==summary).map(el=>[el,el.inert])};
    folds.set(details,record);
    // Keyboard focus moves to the summary before its contents become unavailable.
    if(!desired&&details.contains(document.activeElement)&&document.activeElement!==summary)summary.focus();
    record.children.forEach(([el,inert])=>{el.inert=desired?inert:true;});
    if(reduced.matches||document.hidden){finishFold(details,record);return;}
    details.style.height='auto';details.open=false;const closed=details.getBoundingClientRect().height;
    details.open=true;const expanded=details.getBoundingClientRect().height;
    details.dataset.wbOpen=String(desired);details.style.overflow='hidden';details.style.height=start+'px';
    record.animation=play(details,[{height:start+'px'},{height:(desired?expanded:closed)+'px'}],{duration:420,easing:ease});
    if(record.animation)record.animation.finished.then(()=>finishFold(details,record),()=>{});else finishFold(details,record);
  }
  function finishAll(){
    for(const a of [...animations])a.finish();
    for(const [details,record]of folds)finishFold(details,record);
  }
  function init(){
    if(ready)return;ready=true;nav=document.getElementById('main-nav');
    if(nav){indicator=document.createElement('span');indicator.className='workbench-nav-indicator';indicator.setAttribute('aria-hidden','true');nav.prepend(indicator);nav.dataset.motionReady='true';new ResizeObserver(()=>updateNavigation(false)).observe(nav);}
    document.addEventListener('click',event=>{
      if(event.defaultPrevented||event.button!==0)return;
      const summary=event.target.closest?.('#workspace details>summary');
      if(!summary||event.target.closest('a,button,input,select,textarea'))return;
      event.preventDefault();toggleDetails(summary.parentElement);
    });
    reduced.addEventListener('change',()=>{if(reduced.matches)finishAll();});
    document.addEventListener('visibilitychange',()=>{if(document.hidden)finishAll();});
  }
  root.DeskBotWorkbenchMotion={init,afterRender,arrive,navigate:render=>render(),
    state:()=>({route:lastRoute,reduced:reduced.matches,activeAnimations:[...animations].filter(a=>a.playState==='running').length,folds:folds.size,entrances,waitingForContent:!!arrivalObserver})};
})(window);
