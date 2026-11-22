#pragma once
#include "expression_emote/emote_assets.h"
#include "gfx.h"

namespace deskbot_asset_layout {
// Called by the mounted-resource owner; all registry access uses Emote's lock.
bool Configure(emote_handle_t handle);
void Reset(emote_handle_t handle);
bool FullFrame(emote_handle_t handle, const uint8_t* data, size_t size);
esp_err_t Play(emote_handle_t handle, const char* emotion);
void OnFrame(emote_handle_t handle, gfx_player_event_t event, const void* object);
#if CONFIG_DESKBOT_ROLE_PLAYER_ENABLE
// Role clips use mounted bytes directly; layout lookup requires a valid manifest.
bool Lookup(emote_handle_t handle, const uint8_t* data, size_t bytes, bool& full);
esp_err_t PlayClip(emote_handle_t handle, const uint8_t* data, size_t bytes,
                   uint16_t records, uint8_t fps, uint16_t start = 0);
#endif
}  // namespace deskbot_asset_layout
