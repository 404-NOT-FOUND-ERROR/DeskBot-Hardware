#pragma once
#include "studio_expression_request.h"
namespace deskbot_studio_expression {
Hooks AdapterHooks();
bool Poll(char* output, size_t capacity);
}  // namespace deskbot_studio_expression
