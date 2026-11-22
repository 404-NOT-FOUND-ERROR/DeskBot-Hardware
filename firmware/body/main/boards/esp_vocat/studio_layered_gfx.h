#pragma once
#include "widget/gfx_anim.h"
#ifdef __cplusplus
extern "C" {
#endif
// Callbacks run only under the existing GFX/Emote lock. Draw uses exclusive
// right/bottom coordinates and writes only into the owner's current tile.
typedef struct { int16_t x,y,w,h; } gfx_anim_surface_area_t;
typedef struct {
    void* context;
    esp_err_t (*tick)(void* context);
    esp_err_t (*draw)(void* context,int x,int y,int w,int h,uint16_t* out,int stride,bool swap);
    void (*detached)(void* context);
    // Optional local dirty rectangles. GFX redraws all overlapping objects,
    // including subtitles, and retains its existing tile/flush ownership.
    unsigned (*damage)(void* context,gfx_anim_surface_area_t* areas);
} gfx_anim_surface_t;
esp_err_t gfx_anim_set_surface(gfx_obj_t* obj,const gfx_anim_surface_t* surface,uint16_t width,uint16_t height,uint8_t fps);
esp_err_t gfx_anim_clear_surface(gfx_obj_t* obj);
#ifdef __cplusplus
}
#endif
