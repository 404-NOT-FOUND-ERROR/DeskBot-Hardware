#pragma once

namespace deskbot_studio_catalog {
// Mirrored metadata. Offline audit compares every binding with SetEmotion;
// execution continues to use its original map.
struct Binding { const char* semantic; const char* action; };
constexpr Binding kBindings[] = {
    {"happy", "shark_head"}, {"laughing", "shark_head"},
    {"funny", "shark_head"}, {"loving", "shark_head"},
    {"confident", "shark_head"}, {"delicious", "shark_head"},
    {"thinking", "shark_head"}, {"embarrassed", "cat_nuzzle"},
    {"sad", "shark_head_decay"}, {"crying", "shark_head_decay"},
    {"sleepy", "shark_head_decay"}, {"silly", "look_around"},
    {"confused", "look_around"}, {"angry", "beat_swing"},
    {"surprised", "look_around"}, {"shocked", "look_around"},
    {"winking", "cat_nuzzle"}, {"relaxed", "look_around"},
};
struct Action { const char* id; unsigned value; };
constexpr Action kActions[] = {
    {"shark_head", 0}, {"shark_head_decay", 1}, {"look_around", 2},
    {"beat_swing", 3}, {"cat_nuzzle", 4},
};
}  // namespace deskbot_studio_catalog
