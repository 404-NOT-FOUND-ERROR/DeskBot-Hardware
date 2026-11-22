/* Match GFX's increasing-X, original-then-mirror writes, including overlap. */
(function(root){
  'use strict';
  function project(source,width,height){
    const span=340,cw=Math.min(width,span),out=new Uint8ClampedArray(span*height*4);
    if(!Number.isInteger(width)||!Number.isInteger(height)||width<1||width>360||height<1||height>360||source.length!==width*height*4)throw Error('动画像素尺寸无效');
    for(let y=0;y<height;y++)for(let x=0;x<cw;x++){
      const s=(y*width+x)*4;if(!source[s+3])continue;
      for(const target of [x,span-1-x]){
        const d=(y*span+target)*4;out[d]=source[s];out[d+1]=source[s+1];out[d+2]=source[s+2];out[d+3]=255;
      }
    }
    return out;
  }
  const cache=new WeakMap();
  function draw(ctx,image,sx,sy,width,height){
    const cw=Math.min(width,340),top=Math.floor((360-height)/2)+10;
    if(width<=170){
      ctx.drawImage(image,sx,sy,cw,height,10,top,cw,height);
      ctx.save();ctx.translate(350,0);ctx.scale(-1,1);ctx.drawImage(image,sx,sy,cw,height,0,top,cw,height);ctx.restore();return;
    }
    const key=[sx,sy,width,height].join(':');let row=cache.get(image);
    if(!row||row.key!==key){
      const source=document.createElement('canvas');source.width=width;source.height=height;
      const input=source.getContext('2d',{willReadFrequently:true});input.drawImage(image,sx,sy,width,height,0,0,width,height);
      const canvas=document.createElement('canvas');canvas.width=340;canvas.height=height;
      const output=canvas.getContext('2d'),pixels=output.createImageData(340,height);
      pixels.data.set(project(input.getImageData(0,0,width,height).data,width,height));output.putImageData(pixels,0,0);
      row={key,canvas};cache.set(image,row);
    }
    ctx.drawImage(row.canvas,10,top);
  }
  const api={project,draw};if(typeof module==='object'&&module.exports)module.exports=api;else root.DeskBotMirrorRender=api;
})(typeof window==='object'?window:globalThis);
