#pragma once
#include "expression_emote/emote_assets.h"

namespace deskbot_asset_layout::manifest {
struct Entry { const uint8_t* data; size_t bytes; bool full; };
struct Table { Entry* entries = nullptr; size_t count = 0; };
enum class Result { Absent, Applied, Invalid };
// Resource owner holds the recursive Emote lock throughout load/reset.
Result Load(emote_handle_t handle, Table& table);
void Clear(Table& table);
bool Full(const Table& table, const uint8_t* data, size_t bytes, bool legacy);
}  // namespace deskbot_asset_layout::manifest
