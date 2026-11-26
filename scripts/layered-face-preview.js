/* DFM1 preview: the installed binary is the only animation source. */
'use strict';
(() => {
const mix=(a,b,u)=>a+(b-a)*u,clamp=x=>Math.max(0,Math.min(1,x)),ease=x=>(x=clamp(x),x*x*(3-2*x));
const canvas=()=>Object.assign(document.createElement('canvas'),{width:360,height:360});
function hue(a,b,u){
  if(u<=0)return a;if(u>=1)return b;
  const hsv=c=>{let [r,g,b]=c.map(x=>x/255),v=Math.max(r,g,b),m=Math.min(r,g,b),d=v-m;
    return [!d?0:(v===r?((g-b)/d+6)%6:v===g?(b-r)/d+2:(r-g)/d+4)/6,v?d/v:0,v];};
  const x=hsv(a),y=hsv(b),h=(x[0]+(((y[0]-x[0]+1.5)%1)-.5)*u+1)%1,s=mix(x[1],y[1],u),v=mix(x[2],y[2],u);
  return [5,3,1].map(n=>{let k=(n+h*6)%6;return Math.round(255*v*(1-s*Math.max(0,Math.min(k,4-k,1))));});
}
class LayeredFace {
  constructor(bytes){
    this.bytes=bytes;this.view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);this.cache=new Map();
    const v=this.view,u32=at=>v.getUint32(at,true),u16=at=>v.getUint16(at,true);
    if(new TextDecoder().decode(bytes.slice(0,4))!=='DFM1'||u16(6)!==360||u32(12)!==bytes.length)throw Error('分层资源版本不匹配');
    this.sprites=Array.from({length:u32(16)},(_,i)=>{let p=u32(20)+i*24;return{w:u16(p),h:u16(p+2),channels:bytes[p+4],rows:u32(p+8),data:u32(p+12),bytes:u32(p+16)};});
    this.clips={};this.names=[];for(let i=0;i<u32(24);i++){let p=u32(28)+i*48,name=new TextDecoder().decode(bytes.slice(p,p+32)).split('\0')[0];
      this.names.push(name);this.clips[name]={first:u32(p+32),count:u32(p+36),body:u16(p+40),loop:!!bytes[p+42],mirror:!!bytes[p+43],color:[...bytes.slice(p+44,p+47)]};}
    this.flags=u32(48);this.keys=u32(36);this.current=this.wanted='idle';this.phase='idle';this.clock=0;this.planes=Array.from({length:4},canvas);
    this.scratch=canvas();this.eyeMask=canvas();this.output=canvas();this.pose=this.bodyPose(0);
  }
  sprite(index){
    if(this.cache.has(index))return this.cache.get(index);
    const s=this.sprites[index],pixels=new Uint8ClampedArray(s.w*s.h*4);
    for(let y=0;y<s.h;y++){let at=this.view.getUint32(s.rows+y*4,true)+s.data,end=this.view.getUint32(s.rows+(y+1)*4,true)+s.data,x=0;
      while(at<end){let token=this.bytes[at++],n,color;
        if(s.channels===1){n=(token&15)+1;if(n===16)n+=this.bytes[at++];color=[255,255,255,(token>>4)*17];}
        else{n=token;let [r,g,b,a]=this.bytes.slice(at,at+4);at+=4;color=[a?Math.round(r*255/a):0,a?Math.round(g*255/a):0,a?Math.round(b*255/a):0,a];}
        for(let j=0;j<n;j++)pixels.set(color,(y*s.w+x++)*4);
      }if(x!==s.w)throw Error('分层资源行长度不符');}
    const c=document.createElement('canvas');c.width=s.w;c.height=s.h;c.getContext('2d').putImageData(new ImageData(pixels,s.w,s.h),0,0);
    this.cache.set(index,c);if(this.cache.size>32)this.cache.delete(this.cache.keys().next().value);return c;
  }
  ref(clip,frame,layer){
    const original=clip.mirror&&layer<4?(1-Math.floor(layer/2))*2+layer%2:layer,p=this.keys+frame*100+original*20,index=this.view.getUint16(p,true);
    if(index===65535)return null;let r=[4,8,12,16].map(x=>this.view.getFloat32(p+x,true)),flip=!!this.bytes[p+2];
    if(clip.mirror){r=[360-r[2],r[1],360-r[0],r[3]];flip=!flip;}return {index,r,flip};
  }
  between(a,b,u){return{a,b,u,r:a&&b?a.r.map((v,i)=>mix(v,b.r[i],u)):(a||b)?.r};}
  bodyPose(progress){let c=this.clips[this.current],t=this.flags&2?Math.min(progress*c.body/(c.body-1),1)*(c.count-1):progress*c.count,i=Math.floor(t)%c.count,j=this.flags&2?Math.min(i+1,c.count-1):(i+1)%c.count,u=t-Math.floor(t);
    return {eyes:Array.from({length:4},(_,l)=>this.between(this.ref(c,c.first+i,l),this.ref(c,c.first+j,l),u)),
      accent:[this.ref(c,c.first+i,4),this.ref(c,c.first+j,4)],au:u,color:c.color};}
  transitionPose(progress){let c=this.clips[this.current];return{eyes:Array.from({length:4},(_,l)=>{
      let eye=Math.floor(l/2),pupil=l%2,u=ease((progress-(pupil?eye*35/360:.12+eye*25/360))/(pupil?.75:.8));
      return this.between(this.frozen[l],this.ref(c,c.first,l),u);}),accent:[this.frozen[4],this.ref(c,c.first,4)],
      au:ease((progress-.12)/.8),color:hue(this.fromColor,c.color,ease((progress-.08)/.92))};}
  drawRef(ctx,ref,r,alpha){if(!ref||!alpha||!r)return;let [x,y,right,bottom]=r,w=right-x,h=bottom-y;
    ctx.save();ctx.globalAlpha=alpha;ctx.imageSmoothingEnabled=true;ctx.imageSmoothingQuality='high';
    if(ref.flip){ctx.translate(x+w,y);ctx.scale(-1,1);ctx.drawImage(ref.image||this.sprite(ref.index),0,0,w,h);}
    else ctx.drawImage(ref.image||this.sprite(ref.index),x,y,w,h);ctx.restore();}
  drawBlend(ctx,blend){ctx.clearRect(0,0,360,360);ctx.globalCompositeOperation='source-over';
    this.drawRef(ctx,blend.a,blend.r,1-blend.u);ctx.globalCompositeOperation='lighter';this.drawRef(ctx,blend.b,blend.r,blend.u);ctx.globalCompositeOperation='source-over';}
  drawAccent(ctx,pose){ctx.clearRect(0,0,360,360);ctx.globalCompositeOperation='source-over';
    this.drawRef(ctx,pose.accent[0],pose.accent[0]?.r,1-pose.au);ctx.globalCompositeOperation='lighter';
    this.drawRef(ctx,pose.accent[1],pose.accent[1]?.r,pose.au);ctx.globalCompositeOperation='source-over';}
  freeze(){this.frozen=[];
    for(let l=0;l<5;l++){let refs=l<4?[this.pose.eyes[l].a,this.pose.eyes[l].b]:this.pose.accent,rects=refs.filter(Boolean).map(r=>r.r);
      if(!rects.length){this.frozen.push(null);continue;}
      let r=l<4?this.pose.eyes[l].r:[Math.min(...rects.map(r=>r[0])),Math.min(...rects.map(r=>r[1])),Math.max(...rects.map(r=>r[2])),Math.max(...rects.map(r=>r[3]))];
      r=r.map((v,i)=>i<2?Math.floor(v):Math.ceil(v));let ctx=this.scratch.getContext('2d');if(l<4)this.drawBlend(ctx,this.pose.eyes[l]);else this.drawAccent(ctx,this.pose);
      let c=document.createElement('canvas');c.width=r[2]-r[0];c.height=r[3]-r[1];c.getContext('2d').drawImage(this.scratch,...r.slice(0,2),c.width,c.height,0,0,c.width,c.height);
      this.frozen.push({image:c,r,flip:false});}this.fromColor=[...this.pose.color];}
  begin(target,phase){this.freeze();this.current=this.wanted=target;this.phase=phase;this.clock=0;this.pose=this.transitionPose(0);}
  request(name,replay=true){if(!this.clips[name])throw Error('表情不存在');if(!replay&&name===this.wanted)return;
    if(name==='idle'||name==='neutral')this.cancel();else this.begin(name,'entry');}
  cancel(){if((this.phase==='idle'||this.phase==='exit')&&this.wanted==='idle')return;this.begin('idle','exit');}
  advance(ms){while(ms>0){let c=this.clips[this.current],transition=this.phase==='entry'||this.phase==='exit',duration=transition?360:c.body*40,step=Math.min(ms,duration-this.clock);
      this.clock+=step;ms-=step;this.pose=transition?this.transitionPose(this.clock/360):this.bodyPose(this.clock/duration);
      if(this.clock!==duration)break;this.clock=0;
      if(this.phase==='entry'){this.phase='body';this.pose=this.bodyPose(0);}
      else if(this.phase==='exit'){this.phase='idle';this.current='idle';this.pose=this.bodyPose(0);}
      else if(c.loop||this.phase==='idle')this.pose=this.bodyPose(0);
      else{this.freeze();this.current='idle';this.phase='exit';this.pose=this.transitionPose(0);}}}
  frame(){let ctx=this.output.getContext('2d');ctx.fillStyle=`rgb(${this.pose.color})`;ctx.fillRect(0,0,360,360);
    let sc=this.scratch.getContext('2d');this.drawAccent(sc,this.pose);ctx.drawImage(this.scratch,0,0);
    for(let l=0;l<4;l++){this.drawBlend(sc,this.pose.eyes[l]);sc.globalCompositeOperation='source-in';sc.fillStyle=l%2?'#000':'#fff';sc.fillRect(0,0,360,360);if(this.flags&1){if(l%2){sc.globalCompositeOperation='destination-in';sc.drawImage(this.eyeMask,0,0);}else{let m=this.eyeMask.getContext('2d');m.clearRect(0,0,360,360);m.drawImage(this.scratch,0,0);}}sc.globalCompositeOperation='source-over';ctx.drawImage(this.scratch,0,0);}
    let im=ctx.getImageData(0,0,360,360),p=im.data;for(let i=0;i<p.length;i+=4){p[i]=(p[i]>>3)*255/31;p[i+1]=(p[i+1]>>2)*255/63;p[i+2]=(p[i+2]>>3)*255/31;}ctx.putImageData(im,0,0);return this.output;}
}
window.DeskBotLayeredFace=LayeredFace;
})();
