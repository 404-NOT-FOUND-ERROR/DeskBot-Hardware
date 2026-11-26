// Compile the production GFX draw function against decoded real listen.eaf
// blocks. The fixture uses the bounded Python decoder, not synthetic pixels.
#include "listen_overlay_rgb565.h"
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <vector>

using esp_err_t = int;
using gfx_coord_t = int;
using gfx_mirror_mode_t = int;
constexpr int ESP_OK=0, GFX_OBJ_TYPE_ANIMATION=3;
#define ESP_LOGE(...) ((void)0)
#define GFX_BUFFER_OFFSET_16BPP(p,y,s,x) ((uint8_t*)(p)+((y)*(s)+(x))*2)
#define GFX_BUFFER_OFFSET_4BPP(p,y,s,x) ((uint8_t*)(p)+((y)*(s)+(x))/2)
#define GFX_BUFFER_OFFSET_8BPP(p,y,s,x) ((uint8_t*)(p)+(y)*(s)+(x))
union gfx_color_t { uint16_t full; };
struct gfx_area_t { int x1,y1,x2,y2; };
struct eaf_header_t {
    int width,height,block_height,blocks,bit_depth;
    const uint8_t* palette;
    const uint32_t* block_len;
};
struct gfx_anim_t {
    struct {
        esp_err_t (*draw)(void*,int,int,int,int,uint16_t*,int,bool)=nullptr;
        void* context=nullptr;
    } surface;
    void* file_desc=nullptr;
    struct {
        const void* frame_data;
        eaf_header_t header;
        uint8_t* pixel_buffer;
        uint32_t* block_offsets;
        uint32_t* color_palette;
        int last_block;
    } frame{};
    unsigned current_frame=0;
    bool white_mask=true;
    gfx_mirror_mode_t mirror_mode=0;
    int16_t mirror_offset=0;
};
struct gfx_obj_t {
    int type=GFX_OBJ_TYPE_ANIMATION;
    gfx_anim_t* src;
    struct { int x,y,width,height; } geometry;
};
static void gfx_obj_calc_pos_in_parent(gfx_obj_t*) {}
static bool gfx_area_intersect(gfx_area_t* out,const gfx_area_t* a,const gfx_area_t* b) {
    *out={std::max(a->x1,b->x1),std::max(a->y1,b->y1),std::min(a->x2,b->x2),std::min(a->y2,b->y2)};
    return out->x1<out->x2 && out->y1<out->y2;
}
static bool eaf_palette_get_color(const eaf_header_t* h,uint8_t index,bool swap,gfx_color_t* out) {
    const auto* c=h->palette+index*4;
    if(!(c[0]|c[1]|c[2]|c[3])) return true;
    uint16_t color=uint16_t((c[2]>>3)<<11 | (c[1]>>2)<<5 | (c[0]>>3));
    out->full=swap?uint16_t(color>>8 | color<<8):color;
    return false;
}
static esp_err_t eaf_decode_block(const eaf_header_t*,const uint8_t* data,int bytes,uint8_t* out,bool) {
    std::memcpy(out,data,size_t(bytes));return ESP_OK;
}
// Disabled for this opt-in listen object. Reaching the ordinary EAF renderer
// would silently reintroduce the black matte, so make that a test failure.
static esp_err_t gfx_anim_render_pixels(int,gfx_color_t*,int,const uint8_t*,int,
    const eaf_header_t*,uint32_t*,gfx_area_t*,bool,gfx_mirror_mode_t,int16_t,int) {
    assert(false);return -1;
}
#include "listen_gfx_draw.inc"

static uint32_t Read32(std::ifstream& in) {
    uint8_t b[4];in.read(reinterpret_cast<char*>(b),4);assert(in);
    return unsigned(b[0]) | unsigned(b[1])<<8 | unsigned(b[2])<<16 | unsigned(b[3])<<24;
}
static uint16_t Swap(uint16_t p) { return uint16_t(p>>8 | p<<8); }
static uint16_t Expected(uint16_t bg,uint16_t mask) {
    unsigned out=0;
    for(const auto& pair:{std::pair<unsigned,unsigned>{11,31},{5,63},{0,31}}) {
        unsigned channel=(bg>>pair.first)&pair.second, alpha=(mask>>pair.first)&pair.second;
        out|=(channel+((pair.second-channel)*alpha+pair.second/2)/pair.second)<<pair.first;
    }
    return uint16_t(out);
}
int main(int argc,char** argv) {
    assert(argc==2);std::ifstream in(argv[1],std::ios::binary);assert(in);
    const unsigned count=Read32(in);assert(count==50);unsigned checks=0;
    for(unsigned frame=0;frame<count;++frame) {
        const unsigned w=Read32(in),h=Read32(in),depth=Read32(in),bh=Read32(in),blocks=Read32(in);
        assert(w==100 && h==46 && (depth==4 || depth==8));
        const unsigned divisor=depth==4?2:1;
        std::vector<uint8_t> palette((1u<<depth)*4);in.read(reinterpret_cast<char*>(palette.data()),palette.size());
        std::vector<uint32_t> lengths(blocks),offsets(blocks);std::vector<uint8_t> raw;
        for(unsigned b=0;b<blocks;++b) {
            unsigned n=Read32(in);assert(n==w*bh/divisor);lengths[b]=n;offsets[b]=raw.size();
            const auto before=raw.size();raw.resize(before+n);in.read(reinterpret_cast<char*>(raw.data()+before),n);assert(in);
        }
        std::vector<uint8_t> decoded(w*bh/divisor);
        uint32_t cached_palette[256]{};
        gfx_anim_t anim{};anim.file_desc=&anim;
        anim.frame={raw.data(),{int(w),int(h),int(bh),int(blocks),int(depth),palette.data(),lengths.data()},
            decoded.data(),offsets.data(),cached_palette,-1};
        gfx_obj_t obj{GFX_OBJ_TYPE_ANIMATION,&anim,{130,25,int(w),int(h)}};
        for(bool swap:{false,true}) for(uint16_t bg:{uint16_t(0x268c),uint16_t(0xf214),uint16_t(0x0021)}) {
            // Odd tile starts, partial blocks, and tiles outside the overlay.
            for(unsigned tile=0;tile<4;++tile) {
                const int x1=tile==0?0:131,y1=tile<2?0:31,x2=tile==0?360:227,y2=tile<2?80:67;
                const unsigned size=unsigned((x2-x1)*(y2-y1));
                std::vector<uint16_t> dest(size+2,0xa55a);
                for(unsigned repeat=0;repeat<2;++repeat) {
                    std::fill(dest.begin()+1,dest.end()-1,swap?Swap(bg):bg);
                    gfx_draw_animation(&obj,x1,y1,x2,y2,dest.data()+1,swap);
                    assert(dest.front()==0xa55a && dest.back()==0xa55a);
                    for(int y=y1;y<y2;++y) for(int x=x1;x<x2;++x) {
                        uint16_t expected=bg;
                        if(x>=130 && x<230 && y>=25 && y<71) {
                            unsigned sx=unsigned(x-130),sy=unsigned(y-25),block=sy/bh;
                            unsigned index=(sy%bh)*w+sx;
                            uint8_t packed=raw[offsets[block]+index/divisor];
                            gfx_color_t mask{};
                            const unsigned palette_index=depth==4?(packed>>(index%2?0:4))&15:packed;
                            if(!eaf_palette_get_color(&anim.frame.header,palette_index,false,&mask))
                                expected=Expected(bg,mask.full);
                        }
                        assert(dest[1+(y-y1)*(x2-x1)+x-x1]==(swap?Swap(expected):expected));++checks;
                    }
                }
            }
        }
    }
    std::cout<<"{\"frames\":"<<count<<",\"pixel_checks\":"<<checks<<",\"swap\":true,\"odd_clip\":true}\n";
}
