#pragma once
#include "studio_catalog.h"
#include <esp_partition.h>
#include "expression_emote/emote_assets.h"

namespace deskbot_studio_catalog {
void Invalidate();
void BuildFromMounted(const esp_partition_t*, emote_handle_t);
bool ReadPage(uint16_t cursor, Page& page);
}  // namespace deskbot_studio_catalog
