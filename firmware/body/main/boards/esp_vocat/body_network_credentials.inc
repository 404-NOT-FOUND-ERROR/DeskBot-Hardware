#include "body_network_credentials.h"
#include <esp_heap_caps.h>
#include <mbedtls/platform_util.h>
#include <mbedtls/sha256.h>
#include <mutex>
#include <new>
#include <nvs.h>

namespace deskbot_body_credentials {
namespace {
constexpr char kHex[] = "0123456789abcdef";
int Nibble(char digit) {
    const char* p = digit ? std::strchr(kHex, digit) : nullptr;
    return p ? static_cast<int>(p - kHex) : -1;
}
void Hex(const uint8_t* input, size_t size, char* output) {
    for (size_t i = 0; i < size; ++i) {
        *output++ = kHex[input[i] >> 4];
        *output++ = kHex[input[i] & 15];
    }
    *output = 0;
}
std::shared_ptr<Profile> Allocate() {
    void* bytes = heap_caps_malloc(sizeof(Profile), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!bytes) return {};
    try {
        return {new (bytes) Profile{}, [](Profile* profile) {
            mbedtls_platform_zeroize(profile, sizeof(Profile));
            heap_caps_free(profile);
        }};
    } catch (const std::bad_alloc&) {
        // The shared pointer frees the PSRAM object if its control allocation fails.
        return {};
    }
}
enum class Probe { None, Queued, Running, Passed, Failed };
const char* ProbeName(Probe probe) {
    switch (probe) {
        case Probe::Queued: return "queued";
        case Probe::Running: return "running";
        case Probe::Passed: return "passed";
        case Probe::Failed: return "failed";
        default: return "none";
    }
}
std::mutex mutex;
Snapshot current;
struct Stage {
    std::shared_ptr<Profile> profile;
    char session[80]{};
    size_t filled = 0;
    uint64_t expires = 0;
    uint64_t ticket = 0;
    uint32_t address_revision = 0;
    Probe probe = Probe::None;
    bool check_active = false;
} stage;
uint64_t last_ticket = 0;
bool initialized = false;
const char* storage = "builtin";
char builtin_id[33]{};
bool write_uncertain = false;

const char* ActiveId() { return current ? current->id : builtin_id; }
void Expire(uint64_t now) {
    if (stage.session[0] && now >= stage.expires) stage = {};
}
const char* Begin(const char* arguments, const char* session, uint64_t now) {
    const char* colon = std::strchr(arguments, ':');
    if (!colon || colon - arguments > 4 || std::strlen(session) >= sizeof(stage.session)) return "invalid";
    char size_text[5]{};
    std::memcpy(size_text, arguments, colon - arguments);
    uint32_t size = 0;
    if (!deskbot_body_network::Number(size_text, size) || size < kHeaderBytes + 2 || size > kMaxBytes) return "invalid";
    if (std::strcmp(colon + 1, ActiveId())) return "conflict";
    auto profile = Allocate();
    if (!profile) return "no_memory";
    stage = {};
    stage.profile = std::move(profile);
    stage.profile->size = size;
    std::strcpy(stage.session, session);
    stage.expires = now + kStageLeaseMs;
    return "staging";
}
const char* Append(const char* arguments) {
    if (stage.probe != Probe::None || stage.profile->id[0]) return "conflict";
    const char* colon = std::strchr(arguments, ':');
    if (!colon || colon - arguments > 4) return "invalid";
    char offset_text[5]{};
    std::memcpy(offset_text, arguments, colon - arguments);
    uint32_t offset = 0;
    const char* data = colon + 1;
    const size_t digits = std::strlen(data);
    if (!deskbot_body_network::Number(offset_text, offset) || !digits || digits > 80 || digits % 2
        || offset != stage.filled || digits / 2 > stage.profile->size - stage.filled) return "invalid";
    uint8_t chunk[40]{};
    for (size_t i = 0; i < digits / 2; ++i) {
        const int hi = Nibble(data[2 * i]), lo = Nibble(data[2 * i + 1]);
        if (hi < 0 || lo < 0) {
            mbedtls_platform_zeroize(chunk, sizeof(chunk));
            return "invalid";
        }
        chunk[i] = static_cast<uint8_t>(hi * 16 + lo);
    }
    std::memcpy(stage.profile->bytes + stage.filled, chunk, digits / 2);
    mbedtls_platform_zeroize(chunk, sizeof(chunk));
    stage.filled += digits / 2;
    if (stage.filled < stage.profile->size) return "staging";
    if (!stage.profile->Decode() || !std::strcmp(stage.profile->id, ActiveId())) {
        stage = {};
        return "invalid";
    }
    return "staged";
}
const char* Save(const char* id, uint32_t address_revision) {
    if (write_uncertain) return "save_uncertain";
    if (std::strcmp(stage.profile->id, id) || !stage.profile->id[0]
        || stage.probe != Probe::Passed || stage.address_revision != address_revision) return "not_verified";
    nvs_handle_t handle = 0;
    if (nvs_open("deskbot_net", NVS_READWRITE, &handle) != ESP_OK) return "save_failed";
    // NVS replaces the whole pair at once. A write error may be durable.
    const bool saved = nvs_set_blob(handle, "credentials", stage.profile->bytes, stage.profile->size) == ESP_OK
        && nvs_commit(handle) == ESP_OK;
    nvs_close(handle);
    if (!saved) {
        write_uncertain = true;
        storage = "write_uncertain";
        stage = {};
        return "save_uncertain";
    }
    current = stage.profile;
    stage = {};
    storage = "saved";
    return "saved";
}
}  // namespace

bool Profile::Decode() {
    if (size < kHeaderBytes + 2 || size > kMaxBytes || std::memcmp(bytes, "DBK1", 4)) return false;
    if (!std::memcmp(bytes + 20, bytes + 52, 32)) return false;
    for (unsigned i = 0; i < 2; ++i) {
        cert_size[i] = static_cast<uint16_t>(bytes[84 + 2 * i] * 256 + bytes[85 + 2 * i]);
        if (!cert_size[i] || cert_size[i] > kMaxCertificate) return false;
    }
    if (size != kHeaderBytes + cert_size[0] + cert_size[1]
        || bytes[kHeaderBytes] != 0x30 || bytes[kHeaderBytes + cert_size[0]] != 0x30) return false;
    // The ID binds both tokens and trust anchors, even when a cert has expired.
    constexpr char domain[] = "deskbot.body_credentials/v1";
    uint8_t digest[32]{};
    mbedtls_sha256_context sha;
    mbedtls_sha256_init(&sha);
    const bool matches = mbedtls_sha256_starts(&sha, 0) == 0
        && mbedtls_sha256_update(&sha, reinterpret_cast<const uint8_t*>(domain), sizeof(domain)) == 0
        && mbedtls_sha256_update(&sha, bytes + 20, size - 20) == 0
        && mbedtls_sha256_finish(&sha, digest) == 0 && !std::memcmp(digest, bytes + 4, 16);
    mbedtls_sha256_free(&sha);
    mbedtls_platform_zeroize(digest, sizeof(digest));
    if (!matches) return false;
    // TLS verifies the DER trust anchor and server name as part of each trial.
    Hex(bytes + 4, 16, id);
    for (unsigned i = 0; i < 2; ++i) {
        std::memcpy(authorization[i], "Bearer ", 7);
        Hex(bytes + 20 + i * 32, 32, authorization[i] + 7);
    }
    return true;
}

bool Initialize(const char* default_id) {
    if (std::strlen(default_id) != 32) return false;
    for (size_t i = 0; i < 32; ++i) if (Nibble(default_id[i]) < 0) return false;
    std::lock_guard<std::mutex> lock(mutex);
    std::strcpy(builtin_id, default_id);
    current.reset();
    stage = {};
    storage = "builtin";
    write_uncertain = false;
    nvs_handle_t handle = 0;
    const auto opened = nvs_open("deskbot_net", NVS_READONLY, &handle);
    if (opened == ESP_OK) {
        size_t size = 0;
        const auto result = nvs_get_blob(handle, "credentials", nullptr, &size);
        if (result == ESP_OK && size >= kHeaderBytes + 2 && size <= kMaxBytes) {
            auto profile = Allocate();
            if (!profile) storage = "read_failed";
            else if (nvs_get_blob(handle, "credentials", profile->bytes, &size) == ESP_OK) {
                profile->size = size;
                if (profile->Decode()) {
                    current = std::move(profile);
                    storage = "restored";
                } else storage = "invalid_record";
            } else storage = "read_failed";
        } else if (result != ESP_ERR_NVS_NOT_FOUND) storage = result == ESP_OK ? "invalid_record" : "read_failed";
        nvs_close(handle);
    } else if (opened != ESP_ERR_NVS_NOT_FOUND) storage = "read_failed";
    initialized = true;
    return true;
}
bool Handle(const char* operation, const char* session, uint64_t now, Reply& reply) {
    const uint32_t address_revision = deskbot_body_network::DesiredRevision();
    std::lock_guard<std::mutex> lock(mutex);
    if (!initialized) return false;
    Expire(now);
    if (stage.session[0] && std::strcmp(stage.session, session)) stage = {};
    if (!std::strcmp(operation, "key-get")) reply.result = "state";
    else if (write_uncertain || !std::strcmp(storage, "read_failed")) reply.result = "save_uncertain";
    else if (!std::strcmp(operation, "key-check")) {
        if (std::strlen(session) >= sizeof(stage.session)) return false;
        stage = {};
        std::strcpy(stage.session, session);
        stage.expires = now + kStageLeaseMs;
        stage.check_active = true;
        stage.probe = Probe::Queued;
        reply.result = "testing";
    } else if (!std::strncmp(operation, "key-begin:", 10)) reply.result = Begin(operation + 10, session, now);
    else if (!stage.profile) {
        reply.result = !std::strncmp(operation, "key-save:", 9) && current
            && !std::strcmp(operation + 9, current->id) ? "unchanged" : "conflict";
    } else {
        if (!std::strncmp(operation, "key-data:", 9)) reply.result = Append(operation + 9);
        else if (!std::strcmp(operation, "key-test") && stage.profile->id[0]) {
            if (stage.probe == Probe::None || stage.probe == Probe::Failed) stage.probe = Probe::Queued;
            reply.result = "testing";
        } else if (!std::strncmp(operation, "key-save:", 9)) reply.result = Save(operation + 9, address_revision);
    }
    if (stage.session[0]) {
        stage.expires = now + kStageLeaseMs;
        if (stage.profile) std::strcpy(reply.candidate, stage.profile->id);
        reply.trial = ProbeName(stage.probe);
    }
    std::strcpy(reply.active, ActiveId());
    reply.storage = storage;
    return true;
}
Snapshot Active() {
    std::lock_guard<std::mutex> lock(mutex);
    return current;
}
Trial TakeTrial(uint64_t now, uint32_t applied_address) {
    std::lock_guard<std::mutex> lock(mutex);
    Expire(now);
    if (stage.probe != Probe::Queued) return {};
    stage.probe = Probe::Running;
    stage.address_revision = applied_address;
    stage.ticket = ++last_ticket;
    return {stage.check_active ? current : stage.profile, applied_address, stage.ticket};
}
void FinishTrial(const Trial& trial, bool touch, bool expression) {
    const uint32_t revision = deskbot_body_network::DesiredRevision();
    std::lock_guard<std::mutex> lock(mutex);
    if (trial.ticket && stage.ticket == trial.ticket && stage.probe == Probe::Running) {
        stage.probe = touch && expression && revision == trial.address_revision ? Probe::Passed : Probe::Failed;
    }
}
}  // namespace deskbot_body_credentials
