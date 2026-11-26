#include "body_network_config.h"
#include <mutex>
#include <nvs.h>

namespace deskbot_body_network {
namespace {
std::mutex config_mutex;
Config defaults{}, desired{};
bool initialized = false, applied = false;
bool write_uncertain = false;
uint32_t applied_revision = 0;
const char* storage = "defaults";
// Only the sole network task writes/uses these HTTP URL buffers.
char touch_url[160]{}, expression_url[160]{};
struct Stored { uint32_t magic = 0x4e455431; Config config{}; };
static_assert(sizeof(Stored) <= 84, "small independent NVS record");
enum class StoreResult { Saved, NotStarted, Uncertain };
StoreResult Store(const Config& candidate) {
    nvs_handle_t handle = 0;
    if (nvs_open("deskbot_net", NVS_READWRITE, &handle) != ESP_OK) return StoreResult::NotStarted;
    Stored record{}; record.config = candidate;
    const bool ok = nvs_set_blob(handle, "profile", &record, sizeof(record)) == ESP_OK && nvs_commit(handle) == ESP_OK;
    // IDF can persist a new blob before returning REMOVE_FAILED; commit errors
    // can also leave durable data. Only a new boot resolves the NVS revision.
    nvs_close(handle); return ok ? StoreResult::Saved : StoreResult::Uncertain;
}
}
bool Initialize(const char* host, uint16_t touch, uint16_t expression) {
    std::lock_guard<std::mutex> lock(config_mutex);
    if (!Host(host) || !touch || !expression || touch == expression) return false;
    defaults = {}; std::strcpy(defaults.host, host); defaults.touch_port = touch; defaults.expression_port = expression;
    desired = defaults; applied = false; write_uncertain = false; storage = "defaults";
    nvs_handle_t handle = 0;
    const auto opened = nvs_open("deskbot_net", NVS_READONLY, &handle);
    if (opened == ESP_OK) {
        Stored record{}; size_t size = sizeof(record);
        const auto result = nvs_get_blob(handle, "profile", &record, &size);
        nvs_close(handle);
        if (result == ESP_OK && size == sizeof(record) && record.magic == Stored{}.magic
            && std::memchr(record.config.host, 0, sizeof(record.config.host)) && Valid(record.config)
            && record.config.revision > 0) {
            desired = record.config;
            // A saved reset selects the defaults of the installed App.
            if (!desired.saved) { desired = defaults; desired.revision = record.config.revision; }
            storage = "restored";
        } else if (result != ESP_ERR_NVS_NOT_FOUND) storage = "invalid_record";
    } else if (opened != ESP_ERR_NVS_NOT_FOUND) storage = "read_failed";
    initialized = true; return true;
}
bool Handle(const char* operation, Reply& reply) {
    std::lock_guard<std::mutex> lock(config_mutex);
    if (!initialized) return false;
    Command command{}; const char* result = "invalid";
    if (Decode(operation, command)) {
        result = "state";
        if (command.kind != Kind::Read) {
            Config candidate = command.kind == Kind::Reset ? defaults : command.config;
            const bool same = Same(candidate, desired);
            if (write_uncertain) result = "save_uncertain";
            else if (same && (command.expected == desired.revision
                || (desired.revision && command.expected == desired.revision - 1))) result = "unchanged";
            else if (command.expected != desired.revision) result = "conflict";
            else if (desired.revision == UINT32_MAX) result = "revision_exhausted";
            else {
                candidate.revision = desired.revision + 1;
                const auto stored = Store(candidate);
                if (stored == StoreResult::Saved) { desired = candidate; storage = "saved"; result = "saved"; }
                else if (stored == StoreResult::NotStarted) result = "save_failed";
                else { write_uncertain = true; storage = "write_uncertain"; result = "save_uncertain"; }
            }
        }
    }
    reply = {desired, applied_revision, applied, result, storage}; return true;
}
bool ApplyUrls() {
    std::lock_guard<std::mutex> lock(config_mutex);
    if (!initialized || (applied && applied_revision == desired.revision)) return false;
    std::snprintf(touch_url, sizeof(touch_url), "https://%s:%u/api/hardware/head-touch", desired.host, static_cast<unsigned>(desired.touch_port));
    std::snprintf(expression_url, sizeof(expression_url), "https://%s:%u/api/hardware/expression/exchange", desired.host, static_cast<unsigned>(desired.expression_port));
    applied_revision = desired.revision; applied = true; return true;
}
const char* TouchUrl() { return touch_url; }
const char* ExpressionUrl() { return expression_url; }
uint32_t DesiredRevision() {
    std::lock_guard<std::mutex> lock(config_mutex);
    return desired.revision;
}
uint32_t AppliedRevision() {
    // ApplyUrls and this read are both owned by the network worker.
    return applied_revision;
}
}  // namespace deskbot_body_network
