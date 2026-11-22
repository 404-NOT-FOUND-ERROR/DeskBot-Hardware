#pragma once
#include <cstddef>
#include <cstdint>
using esp_err_t = int;
constexpr esp_err_t ESP_OK = 0;
struct esp_partition_t { size_t size; };
esp_err_t esp_partition_read(const esp_partition_t*, size_t, void*, size_t);
