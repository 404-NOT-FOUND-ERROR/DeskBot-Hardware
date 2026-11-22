/* Native 480 px layered candidate. Background and transitions are drawn once;
   only exact matching cropped coverage sprites share storage (including flips). */
"use strict";
(() => {
  const pack = window.AIRBOAT_OPTIMIZED_PACK, data = window.AIRBOAT_EXPRESSIONS;
  if (!pack || pack.canvas !== 480 || pack.alpha_levels !== 16) throw Error("高清图层包版本不匹配");
  const bytes = Uint8Array.from(atob(pack.payload), c => c.charCodeAt(0));
  const view = new DataView(bytes.buffer);
  if (String.fromCharCode(...bytes.slice(0,4)) !== "AFM1" || view.getUint32(4,true) !== 1 ||
      view.getUint32(8,true) !== pack.sprites.length || view.getUint32(12,true) !== bytes.length-16)
    throw Error("高清图层包边界错误");
  const cache = new Map();
  function sprite(index) {
    if (cache.has(index)) return cache.get(index);
    const s = pack.sprites[index];
    if (!s || s.w < 1 || s.h < 1 || s.w > 480 || s.h > 480 || (s.palette && s.bytes%2) || s.offset < 0 || 16+s.offset+s.bytes > bytes.length)
      throw Error("高清精灵边界错误");
    const rgba = new Uint8ClampedArray(s.w*s.h*4);
    let written = 0;
    for (let i = 16+s.offset; i < 16+s.offset+s.bytes;) {
      const token=bytes[i++];
      let count, value;
      if (!s.palette) {
        count=(token&15)+1;value=token>>4;
        if(count===16) { if(i>=16+s.offset+s.bytes)throw Error("高清精灵长游程不完整");count+=bytes[i++]; }
      } else { count=token;value=bytes[i++]; }
      if (!count || written+count > s.w*s.h || (s.palette ? value >= s.palette.length : value > 15))
        throw Error("高清精灵 RLE 错误");
      const color = s.palette ? s.palette[value] : [255,255,255,value*17];
      for (let j=0; j<count; ++j) rgba.set(color,(written+j)*4);
      written += count;
    }
    if (written !== s.w*s.h) throw Error("高清精灵长度不完整");
    const canvas = document.createElement("canvas"); canvas.width=s.w; canvas.height=s.h;
    canvas.getContext("2d").putImageData(new ImageData(rgba,s.w,s.h),0,0);
    cache.set(index,canvas);
    if (cache.size > 32) cache.delete(cache.keys().next().value);
    return canvas;
  }
  const ease = n => (n=Math.min(1,Math.max(0,n)),n*n*(3-2*n));
  function rgb(hex) { return [1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)); }
  function hsv(hex) {
    const [r,g,b]=rgb(hex).map(n=>n/255),max=Math.max(r,g,b),min=Math.min(r,g,b),d=max-min;
    let h=!d ? 0 : max===r ? ((g-b)/d+6)%6 : max===g ? (b-r)/d+2 : (r-g)/d+4;
    return [h/6,max ? d/max : 0,max];
  }
  function hue(a,b,u) {
    if (u<=0) return a; if (u>=1) return b;
    const x=hsv(a),y=hsv(b),h=(x[0]+((((y[0]-x[0]+1.5)%1)-.5)*u)+1)%1;
    const s=x[1]+(y[1]-x[1])*u,v=x[2]+(y[2]-x[2])*u;
    const channels=[5,3,1].map(n=>{const k=(n+h*6)%6;return Math.round(255*v*(1-s*Math.max(0,Math.min(k,4-k,1))));});
    return `rgb(${channels.join(",")})`;
  }
  const mix = (a,b,u) => a+(b-a)*u;
  const canvas = () => {const c=document.createElement("canvas");c.width=c.height=480;return c;};
  class Optimized {
    constructor(asset) {
      this.asset=asset; this.clip=pack.clips[asset.semantic];
      this.output=canvas(); this.layer=canvas(); this.cache=new Map();
      if (!this.clip) throw Error("高清表情不存在");
    }
    static decodeSprite(index) { return sprite(index); }
    static get statistics() { return {...pack.sharing,totalBytes:bytes.length+pack.catalog_bytes,decodedSpriteCache:cache.size}; }
    pose(frame) {
      const c=this.clip, n=pack.transition_frames, body=c.body_frames, base=pack.clips.idle;
      if (frame<n) return {a:base.keyframes[0],b:c.keyframes[0],u:frame/(n-1),from:base.color,to:c.color,transition:true};
      if (frame>=n+body) return {a:c.keyframes[0],b:base.keyframes[0],u:(frame-n-body)/(n-1),from:c.color,to:base.color,transition:true};
      const t=(frame-n)*c.keyframes.length/body,i=Math.floor(t);
      return {a:c.keyframes[i],b:c.keyframes[(i+1)%c.keyframes.length],u:t-i,from:c.color,to:c.color,transition:false};
    }
    drawSprite(ctx,ref,x,y,w,h,opacity) {
      if (!ref || !opacity) return;
      ctx.save(); ctx.globalAlpha=opacity;
      if (ref[5]) { ctx.translate(x+w,y);ctx.scale(-1,1);ctx.drawImage(sprite(ref[0]),0,0,w,h); }
      else ctx.drawImage(sprite(ref[0]),x,y,w,h);
      ctx.restore();
    }
    layerBetween(ctx,a,b,u,color) {
      if (!a && !b) return;
      const target=a&&b ? [1,2,3,4].map(i=>mix(a[i],b[i],u)) : (a||b).slice(1,5);
      const [x,y,r,d]=target,w=Math.max(.1,r-x),h=Math.max(.1,d-y),lc=this.layer.getContext("2d");
      lc.clearRect(0,0,480,480);lc.imageSmoothingEnabled=true;lc.imageSmoothingQuality="high";
      // Premultiplied alpha is added, then colored. Solid pupils remain solid
      // while the layer's own box moves; there is no full-face crossfade.
      lc.globalCompositeOperation="source-over";
      this.drawSprite(lc,a,x,y,w,h,1-u);
      lc.globalCompositeOperation="lighter";
      this.drawSprite(lc,b,x,y,w,h,u);
      lc.globalCompositeOperation="source-in";lc.fillStyle=color;lc.fillRect(0,0,480,480);
      lc.globalCompositeOperation="source-over";
      ctx.drawImage(this.layer,0,0);
    }
    frame(number) {
      number=Math.max(0,Math.min(this.asset.frames-1,number));
      if (this.cache.has(number)) return this.cache.get(number);
      const p=this.pose(number),ctx=this.output.getContext("2d");
      const bg=p.transition ? hue(p.from,p.to,ease((p.u-.08)/.92)) : p.to;
      ctx.fillStyle=bg;ctx.fillRect(0,0,480,480);
      const accentU=p.transition ? ease((p.u-.12)/.8) : p.u;
      const lc=this.layer.getContext("2d");lc.clearRect(0,0,480,480);lc.globalCompositeOperation="source-over";
      for (const [ref,alpha] of [[p.a.accent,1-accentU],[p.b.accent,accentU]]) {
        if (ref) this.drawSprite(lc,ref,ref[1],ref[2],ref[3]-ref[1],ref[4]-ref[2],alpha);
        lc.globalCompositeOperation="lighter";
      }
      lc.globalCompositeOperation="source-over";ctx.drawImage(this.layer,0,0);
      for (let i=0;i<4;++i) {
        const eye=Math.floor(i/2),pupil=i%2;
        const u=p.transition ? ease((p.u-(pupil ? eye*35/360 : .12+eye*25/360))/(pupil ? .75 : .8)) : p.u;
        this.layerBetween(ctx,p.a.layers[i],p.b.layers[i],u,pupil ? "#000" : "#fff");
      }
      // Compare both encodings through the same RGB565 display precision.
      const image=ctx.getImageData(0,0,480,480),pixels=image.data;
      for(let i=0;i<pixels.length;i+=4){
        pixels[i]=((pixels[i]>>3)*255/31)|0;
        pixels[i+1]=((pixels[i+1]>>2)*255/63)|0;
        pixels[i+2]=((pixels[i+2]>>3)*255/31)|0;
      }
      ctx.putImageData(image,0,0);
      const out=canvas();out.getContext("2d").drawImage(this.output,0,0);
      this.cache.set(number,out);if(this.cache.size>3)this.cache.delete(this.cache.keys().next().value);
      return out;
    }
  }
  window.AirboatOptimized=Optimized;
  data.optimized={};
  for (const key of [...Object.keys(pack.clips),"neutral"]) {
    const actual=key==="neutral" ? "idle" : key,c=pack.clips[actual],n=pack.transition_frames;
    const file=`optimized-${actual}.afm`, frames=n*2+c.body_frames;
    const refs=new Set(c.keyframes.flatMap(f=>[...f.layers,f.accent].filter(Boolean).map(r=>r[0])));
    data.assets[file]={width:480,height:480,frames,kind:"optimized",semantic:actual,
      bytes:[...refs].reduce((s,i)=>s+pack.sprites[i].bytes,0),shared:true};
    data.optimized[key]={file,fps:pack.fps,loop:true,range:[n,n+c.body_frames],fill:true,semantic:key,
      segments:{entry:[0,n],performance:[n,n+c.body_frames],exit:[n+c.body_frames,frames]}};
  }
})();
