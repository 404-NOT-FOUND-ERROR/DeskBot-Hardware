const {test}=require('node:test'),assert=require('node:assert/strict');
const {project}=require('../scripts/deskbot-mirror-render.js');
for(const width of [1,160,170,171,200,340,360])for(const transparent of [false,true]){
  test(`GFX column reference width ${width}, transparent ${transparent}`,()=>{
    const height=3,source=new Uint8ClampedArray(width*height*4);
    for(let y=0;y<height;y++)for(let x=0;x<width;x++){const i=(y*width+x)*4;source.set([x%256,Math.floor(x/256),y,transparent&&x%3===0?0:255],i);}
    const actual=project(source,width,height);
    // Independent inverse mapping: the last opaque source column owns the pixel.
    for(let y=0;y<height;y++)for(let p=0;p<340;p++){
      const candidates=[p,339-p].filter(x=>x<Math.min(width,340)&&source[(y*width+x)*4+3]);
      const x=Math.max(-1,...candidates),expected=x<0?[0,0,0,0]:Array.from(source.slice((y*width+x)*4,(y*width+x)*4+4));
      assert.deepEqual(Array.from(actual.slice((y*340+p)*4,(y*340+p)*4+4)),expected);
    }
  });
}
test('Invalid frame sizes rejected',()=>{for(const n of [0,-1,361,1.5])assert.throws(()=>project(new Uint8ClampedArray(4),n,1));});
