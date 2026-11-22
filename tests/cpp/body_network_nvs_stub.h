#pragma once
#include <cassert>
#include <cstring>
#include <vector>
namespace network_fixture {
inline std::vector<uint8_t> committed, staged;
inline bool fail_open = false, fail_set = false, fail_commit = false;
inline bool persist_set_error = false, persist_commit_error = false;
inline unsigned writes = 0, commits = 0;
}
inline int nvs_open(const char* ns, int mode, nvs_handle_t* handle) {
    assert(!std::strcmp(ns, "deskbot_net"));
    if (network_fixture::fail_open) return ESP_FAIL;
    if (mode == NVS_READONLY && network_fixture::committed.empty()) return ESP_ERR_NVS_NOT_FOUND;
    *handle = 1; return ESP_OK;
}
inline int nvs_get_blob(nvs_handle_t, const char* key, void* data, size_t* size) {
    assert(!std::strcmp(key, "profile"));
    if (network_fixture::committed.empty()) return ESP_ERR_NVS_NOT_FOUND;
    if (*size < network_fixture::committed.size()) return ESP_FAIL;
    *size = network_fixture::committed.size(); std::memcpy(data, network_fixture::committed.data(), *size); return ESP_OK;
}
inline int nvs_set_blob(nvs_handle_t, const char* key, const void* data, size_t size) {
    assert(!std::strcmp(key, "profile")); ++network_fixture::writes;
    if (network_fixture::fail_set) {
        if (network_fixture::persist_set_error) {
            network_fixture::committed.assign(static_cast<const uint8_t*>(data), static_cast<const uint8_t*>(data) + size);
            return ESP_ERR_NVS_REMOVE_FAILED;
        }
        return ESP_FAIL;
    }
    network_fixture::staged.assign(static_cast<const uint8_t*>(data), static_cast<const uint8_t*>(data) + size); return ESP_OK;
}
inline int nvs_commit(nvs_handle_t) {
    ++network_fixture::commits;
    if (network_fixture::fail_commit) {
        if (network_fixture::persist_commit_error) network_fixture::committed = network_fixture::staged;
        return ESP_FAIL;
    }
    network_fixture::committed = network_fixture::staged; return ESP_OK;
}
inline void nvs_close(nvs_handle_t) { network_fixture::staged.clear(); }
