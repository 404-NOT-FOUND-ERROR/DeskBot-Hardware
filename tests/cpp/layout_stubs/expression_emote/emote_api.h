#pragma once
#include "expression_emote/emote_assets.h"
#include "gfx.h"
esp_err_t emote_lock(emote_handle_t);
esp_err_t emote_unlock(emote_handle_t);
gfx_obj_t* emote_get_obj_by_name(emote_handle_t, const char*);
esp_err_t emote_set_anim_emoji(emote_handle_t, const char*);
esp_err_t emote_notify_all_refresh(emote_handle_t);
