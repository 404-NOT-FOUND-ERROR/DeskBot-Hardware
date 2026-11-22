#pragma once
#include "esp_partition.h"
using emote_handle_t = void*;
struct emoji_data_t { const void* data; size_t size; uint8_t fps; bool loop; };
struct icon_data_t { const void* data; size_t size; };
esp_err_t emote_get_asset_data_by_name(emote_handle_t, const char*, const uint8_t**, size_t*);
esp_err_t emote_get_icon_data_by_name(emote_handle_t, const char*, icon_data_t**);
esp_err_t emote_get_emoji_data_by_name(emote_handle_t, const char*, emoji_data_t**);
