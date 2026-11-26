#include "body_network_config.inc"
#include "body_network_credentials.inc"
#include "head_touch_wifi.h"
#include "studio_usb_session.h"
#include <cassert>
#include <iostream>
#include <map>
#include <string>
#include <vector>

namespace {
std::map<std::string, std::vector<uint8_t>> committed;
std::string pending_key;
std::vector<uint8_t> pending;
bool commit_uncertain = false;
}
int nvs_open(const char*, int, nvs_handle_t* handle) { *handle = 1; return ESP_OK; }
int nvs_get_blob(nvs_handle_t, const char* key, void* data, size_t* size) {
    if (!committed.count(key)) return ESP_ERR_NVS_NOT_FOUND;
    const auto& bytes = committed.at(key);
    if (data) {
        if (*size < bytes.size()) return ESP_FAIL;
        std::memcpy(data, bytes.data(), bytes.size());
    }
    *size = bytes.size();
    return ESP_OK;
}
int nvs_set_blob(nvs_handle_t, const char* key, const void* data, size_t size) {
    pending_key = key;
    const auto* bytes = static_cast<const uint8_t*>(data);
    pending.assign(bytes, bytes + size);
    return ESP_OK;
}
int nvs_commit(nvs_handle_t) {
    committed[pending_key] = pending;
    return commit_uncertain ? ESP_FAIL : ESP_OK;
}
void nvs_close(nvs_handle_t) { pending.clear(); }

int main() {
    using namespace deskbot_body_network;
    using namespace deskbot_head_touch_wifi;
    assert(Classify(401, true) == Delivery::Retry);
    assert(Classify(409, true) == Delivery::Rejected);
    deskbot_studio_session::Endpoint endpoint;
    unsigned boot = 0;
    auto restart = [&] {
        endpoint = {};
        ++boot;
        assert(endpoint.Initialize(std::string(32, 'a').c_str(), std::string(32, boot == 1 ? 'b' : 'c').c_str()));
        assert(Initialize("10.0.0.1", 4318, 4319));
        endpoint.SetNetworkProvider(Handle);
        assert(deskbot_body_credentials::Initialize(std::string(32, '6').c_str()));
        endpoint.SetCredentialsProvider(deskbot_body_credentials::Handle);
    };
    restart();
    char output[1024];
    for (std::string line; std::getline(std::cin, line);) {
        const auto split = line.find('|');
        const auto now = std::stoull(line.substr(0, split));
        const auto message = line.substr(split + 1);
        ApplyUrls();
        if (!message.empty() && message[0] == '!') {
            if (message == "!boot") restart();
            else if (message == "!uncertain") commit_uncertain = true;
            else {
                const auto trial = deskbot_body_credentials::TakeTrial(now, AppliedRevision());
                assert(trial.ticket);
                deskbot_body_credentials::FinishTrial(trial, true, message == "!pass");
            }
            continue;
        }
        for (char c : message + '\n') {
            if (endpoint.Feed(c, now, output, sizeof(output))) std::cout << output << '\n' << std::flush;
        }
    }
}
