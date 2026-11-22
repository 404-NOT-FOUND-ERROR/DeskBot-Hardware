#pragma once
void catalog_log(const char*, const char*, ...);
#define ESP_LOGW(...) catalog_log(__VA_ARGS__)
#define ESP_LOGI(...) catalog_log(__VA_ARGS__)
