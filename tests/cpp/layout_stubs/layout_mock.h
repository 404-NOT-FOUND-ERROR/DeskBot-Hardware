#pragma once
#include "gfx.h"
#include <string>
namespace layout_mock {
extern gfx_obj_t object;
extern int depth, calls, refreshes, image_events, metadata_events;
extern std::string failure, played;
extern bool covered, render_before_frame;
void Finish(emote_handle_t handle);
}
