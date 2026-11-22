"""Small, reversible procedural surface backend on the accepted GFX source."""


def patch(raw):
    text=raw.decode('utf-8').replace('\r\n','\n')
    def change(before,after):
        nonlocal text
        if text.count(before)!=1: raise ValueError('layered_gfx_anchor_changed: '+before[:60])
        text=text.replace(before,after,1)
    change('#include "widget/gfx_anim.h"','#include "widget/gfx_anim.h"\n#include "widget/gfx_anim_surface.h"')
    change('    gfx_mirror_mode_t mirror_mode;   /*!< Mirror mode */',
           '    gfx_anim_surface_t surface;     /* Optional bounded procedural renderer. */\n    gfx_mirror_mode_t mirror_mode;   /*!< Mirror mode */')
    change('    if (anim->file_desc == NULL) {\n        return;\n    }', '''    if (anim->surface.draw) {
        gfx_obj_calc_pos_in_parent(obj);
        int left = x1 > obj->geometry.x ? x1 : obj->geometry.x;
        int top = y1 > obj->geometry.y ? y1 : obj->geometry.y;
        int right = x2 < obj->geometry.x + obj->geometry.width ? x2 : obj->geometry.x + obj->geometry.width;
        int bottom = y2 < obj->geometry.y + obj->geometry.height ? y2 : obj->geometry.y + obj->geometry.height;
        if (left < right && top < bottom) {
            uint16_t *out = (uint16_t *)dest_buf + (top-y1)*(x2-x1) + left-x1;
            anim->surface.draw(anim->surface.context,left-obj->geometry.x,top-obj->geometry.y,
                               right-left,bottom-top,out,x2-x1,swap);
        }
        return;
    }
    if (anim->file_desc == NULL) {
        return;
    }''')
    change('    if (anim) {\n        if (anim->is_playing)',
           '    if (anim) {\n        gfx_anim_clear_surface(obj);\n        if (anim->is_playing)')
    change('    gfx_core_context_t *ctx = obj->parent_handle;\n    if (anim->current_frame >= anim->end_frame)', '''    gfx_core_context_t *ctx = obj->parent_handle;
    if (anim->surface.tick) {
        if (anim->surface.tick(anim->surface.context) != ESP_OK) return;
        if (ctx->callbacks.update_cb) ctx->callbacks.update_cb(ctx,GFX_PLAYER_EVENT_ONE_FRAME_DONE,obj);
        if (anim->surface.damage) {
            gfx_anim_surface_area_t areas[3];
            unsigned count=anim->surface.damage(anim->surface.context,areas);
            gfx_obj_calc_pos_in_parent(obj);
            for(unsigned i=0;i<count && i<3;++i) {
                int left=areas[i].x>0?areas[i].x:0,top=areas[i].y>0?areas[i].y:0;
                int right=areas[i].x+areas[i].w,bottom=areas[i].y+areas[i].h;
                if(right>obj->geometry.width) right=obj->geometry.width;
                if(bottom>obj->geometry.height) bottom=obj->geometry.height;
                if(left>=right || top>=bottom) continue;
                gfx_area_t area={obj->geometry.x+left,obj->geometry.y+top,
                                 obj->geometry.x+right-1,obj->geometry.y+bottom-1};
                gfx_invalidate_area(ctx,&area);
            }
        } else gfx_obj_invalidate(obj);
        return;
    }
    if (anim->current_frame >= anim->end_frame)''')
    change('    /* Invalidate the old animation */\n    gfx_obj_invalidate(obj);',
           '    gfx_anim_clear_surface(obj);\n    /* Invalidate the old animation */\n    gfx_obj_invalidate(obj);')
    change('    if (anim->file_desc == NULL) {\n        ESP_LOGE(TAG, "Animation source not set");',
           '    if (anim->file_desc == NULL && anim->surface.tick == NULL) {\n        ESP_LOGE(TAG, "Animation source not set");')
    text+='''
esp_err_t gfx_anim_clear_surface(gfx_obj_t *obj)
{
    CHECK_OBJ_TYPE_ANIMATION(obj);
    gfx_anim_t *anim = (gfx_anim_t *)obj->src;
    if (!anim) return ESP_ERR_INVALID_STATE;
    gfx_anim_surface_t previous = anim->surface;
    memset(&anim->surface,0,sizeof(anim->surface));
    if (previous.detached) previous.detached(previous.context);
    return ESP_OK;
}
esp_err_t gfx_anim_set_surface(gfx_obj_t *obj,const gfx_anim_surface_t *surface,uint16_t width,uint16_t height,uint8_t fps)
{
    CHECK_OBJ_TYPE_ANIMATION(obj);
    gfx_anim_t *anim = (gfx_anim_t *)obj->src;
    if (!anim || !surface || !surface->tick || !surface->draw || !width || width>360 || !height || height>360 || !fps || fps>60)
        return ESP_ERR_INVALID_ARG;
    gfx_anim_stop(obj);
    gfx_anim_clear_surface(obj);
    gfx_anim_reset_frame(&anim->frame);
    if (anim->file_desc) { eaf_deinit(anim->file_desc); anim->file_desc=NULL; }
    anim->surface=*surface;
    anim->fps=fps;
    anim->mirror_mode=GFX_MIRROR_DISABLED;
    obj->geometry.width=width;obj->geometry.height=height;
    gfx_timer_set_period(anim->timer,1000/fps);
    gfx_obj_invalidate(obj);
    return ESP_OK;
}
'''
    return text.encode('utf-8')
