#include "magnetic_shell_manager.h"

#include "settings.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <limits>

#include <cJSON.h>
#include <esp_log.h>

namespace {

constexpr char kTag[] = "ShellManager";
constexpr char kSettingsNamespace[] = "mag_shell";
constexpr char kProfilesKey[] = "profiles";
constexpr int kRecognitionWindowMin = 10;
constexpr int kStableNoiseLimit = 40;
constexpr int kLearningNoiseLimit = 35;
constexpr int kMinimumTolerance = 110;
constexpr int kMaximumTolerance = 190;
constexpr size_t kMaximumNameBytes = 36;
constexpr size_t kEventConfirmationSamples = 5;
constexpr int64_t kEventCooldownMs = 1500;

int vector_magnitude(double x, double y, double z)
{
    return static_cast<int>(std::lround(std::sqrt(x * x + y * y + z * z)));
}

int matching_distance(int x, int y, int z, int magnitude,
                      const MagneticShellManager::Profile& profile)
{
    const double dx = static_cast<double>(x - profile.x);
    const double dy = static_cast<double>(y - profile.y);
    const double dz = static_cast<double>(z - profile.z);
    const double dm = static_cast<double>(magnitude - profile.magnitude);
    const double vector_distance_sq = dx * dx + dy * dy + dz * dz;

    // Magnitude remains useful if the head yaws, while the vector direction
    // preserves separation between shells with similarly strong magnets.
    return static_cast<int>(std::lround(
        std::sqrt(0.35 * vector_distance_sq + 0.65 * dm * dm)
    ));
}

const char* event_type_name(MagneticShellManager::EventType type)
{
    switch (type) {
    case MagneticShellManager::EventType::Attached:
        return "attached";
    case MagneticShellManager::EventType::Changed:
        return "changed";
    case MagneticShellManager::EventType::Unknown:
        return "unknown";
    case MagneticShellManager::EventType::Detached:
        return "detached";
    default:
        return "none";
    }
}

std::string truncate_utf8(const std::string& value, size_t maximum_bytes)
{
    if (value.size() <= maximum_bytes) {
        return value;
    }

    size_t end = maximum_bytes;
    while (end > 0 && (static_cast<unsigned char>(value[end]) & 0xC0) == 0x80) {
        --end;
    }
    return value.substr(0, end);
}

} // namespace

MagneticShellManager& MagneticShellManager::GetInstance()
{
    static MagneticShellManager instance;
    return instance;
}

MagneticShellManager::MagneticShellManager()
{
    mutex_ = xSemaphoreCreateMutex();
    if (mutex_ == nullptr) {
        ESP_LOGE(kTag, "Failed to create mutex");
    }
}

MagneticShellManager::~MagneticShellManager()
{
    if (mutex_ != nullptr) {
        vSemaphoreDelete(mutex_);
    }
}

void MagneticShellManager::Initialize()
{
    if (mutex_ == nullptr || xSemaphoreTake(mutex_, pdMS_TO_TICKS(100)) != pdTRUE) {
        return;
    }
    if (!initialized_) {
        LoadProfilesLocked();
        initialized_ = true;
        ESP_LOGI(kTag, "Loaded %u shell profiles", static_cast<unsigned>(profiles_.size()));
    }
    xSemaphoreGive(mutex_);
}

void MagneticShellManager::AddSample(int16_t x, int16_t y, int16_t z, int64_t timestamp_ms)
{
    Initialize();
    if (mutex_ == nullptr || xSemaphoreTake(mutex_, pdMS_TO_TICKS(10)) != pdTRUE) {
        return;
    }

    window_[window_next_] = Sample{x, y, z, timestamp_ms};
    window_next_ = (window_next_ + 1) % window_.size();
    window_count_ = std::min(window_count_ + 1, window_.size());
    has_data_ = true;
    last_sample_ms_ = timestamp_ms;

    if (learning_) {
        learning_sum_x_ += x;
        learning_sum_y_ += y;
        learning_sum_z_ += z;
        learning_sum_x2_ += static_cast<int64_t>(x) * x;
        learning_sum_y2_ += static_cast<int64_t>(y) * y;
        learning_sum_z2_ += static_cast<int64_t>(z) * z;
        ++learning_count_;
        if (learning_count_ >= kLearningSamples) {
            FinishLearningLocked();
        }
    }

    UpdateRecognitionLocked();
    UpdateEventStateLocked(timestamp_ms);
    xSemaphoreGive(mutex_);
}

void MagneticShellManager::NotifySensorUnavailable(int64_t timestamp_ms)
{
    Initialize();
    if (mutex_ == nullptr || xSemaphoreTake(mutex_, pdMS_TO_TICKS(20)) != pdTRUE) {
        return;
    }

    if (committed_event_state_ == RecognitionState::Matched ||
        committed_event_state_ == RecognitionState::Unknown) {
        EmitEventLocked(EventType::Detached, timestamp_ms);
    }
    committed_event_state_ = RecognitionState::NoData;
    committed_event_profile_id_ = 0;
    candidate_event_state_ = RecognitionState::NoData;
    candidate_event_profile_id_ = 0;
    candidate_event_count_ = 0;
    has_data_ = false;
    stable_ = false;
    recognition_ = RecognitionState::NoData;
    matched_profile_id_ = 0;
    matched_name_.clear();
    confidence_ = 0;
    distance_ = 0;
    last_sample_ms_ = timestamp_ms;
    last_result_ = "底座磁场数据已断开";
    xSemaphoreGive(mutex_);
}

bool MagneticShellManager::StartLearning(const std::string& requested_name, std::string* message)
{
    Initialize();
    if (mutex_ == nullptr || xSemaphoreTake(mutex_, pdMS_TO_TICKS(100)) != pdTRUE) {
        if (message != nullptr) {
            *message = "外壳识别模块忙";
        }
        return false;
    }

    bool success = false;
    if (learning_) {
        last_result_ = "正在采样，请保持外壳不动";
    } else if (!has_data_) {
        last_result_ = "没有磁场数据，请确认底座已连接";
    } else if (profiles_.size() >= kMaxProfiles) {
        last_result_ = "外壳列表已满，请先删除一个档案";
    } else {
        learning_name_ = NormalizeNameLocked(requested_name);
        learning_count_ = 0;
        learning_sum_x_ = 0;
        learning_sum_y_ = 0;
        learning_sum_z_ = 0;
        learning_sum_x2_ = 0;
        learning_sum_y2_ = 0;
        learning_sum_z2_ = 0;
        learning_ = true;
        last_result_ = "开始采样，请保持外壳不动";
        success = true;
    }

    if (message != nullptr) {
        *message = last_result_;
    }
    xSemaphoreGive(mutex_);
    return success;
}

bool MagneticShellManager::RenameProfile(int id, const std::string& name, std::string* message)
{
    Initialize();
    if (mutex_ == nullptr || xSemaphoreTake(mutex_, pdMS_TO_TICKS(100)) != pdTRUE) {
        if (message != nullptr) {
            *message = "外壳识别模块忙";
        }
        return false;
    }

    bool success = false;
    const std::string normalized = truncate_utf8(name, kMaximumNameBytes);
    if (normalized.empty()) {
        last_result_ = "名称不能为空";
    } else {
        auto it = std::find_if(profiles_.begin(), profiles_.end(),
                               [id](const Profile& profile) { return profile.id == id; });
        if (it == profiles_.end()) {
            last_result_ = "没有找到这个外壳档案";
        } else {
            it->name = normalized;
            matched_name_ = matched_profile_id_ == id ? normalized : matched_name_;
            success = SaveProfilesLocked();
            last_result_ = success ? "外壳名称已更新" : "名称保存失败";
        }
    }

    if (message != nullptr) {
        *message = last_result_;
    }
    xSemaphoreGive(mutex_);
    return success;
}

bool MagneticShellManager::DeleteProfile(int id, std::string* message)
{
    Initialize();
    if (mutex_ == nullptr || xSemaphoreTake(mutex_, pdMS_TO_TICKS(100)) != pdTRUE) {
        if (message != nullptr) {
            *message = "外壳识别模块忙";
        }
        return false;
    }

    const auto old_size = profiles_.size();
    profiles_.erase(std::remove_if(profiles_.begin(), profiles_.end(),
                                   [id](const Profile& profile) { return profile.id == id; }),
                    profiles_.end());
    bool success = profiles_.size() != old_size;
    if (!success) {
        last_result_ = "没有找到这个外壳档案";
    } else {
        success = SaveProfilesLocked();
        last_result_ = success ? "外壳档案已删除" : "删除保存失败";
        UpdateRecognitionLocked();
    }

    if (message != nullptr) {
        *message = last_result_;
    }
    xSemaphoreGive(mutex_);
    return success;
}

MagneticShellManager::Snapshot MagneticShellManager::GetSnapshot(bool include_history) const
{
    Snapshot snapshot;
    if (mutex_ == nullptr || xSemaphoreTake(mutex_, pdMS_TO_TICKS(20)) != pdTRUE) {
        return snapshot;
    }

    snapshot.has_data = has_data_;
    snapshot.stable = stable_;
    snapshot.x = filtered_x_;
    snapshot.y = filtered_y_;
    snapshot.z = filtered_z_;
    snapshot.magnitude = filtered_magnitude_;
    snapshot.noise = noise_;
    snapshot.last_sample_ms = last_sample_ms_;
    snapshot.recognition = recognition_;
    snapshot.matched_profile_id = matched_profile_id_;
    snapshot.matched_name = matched_name_;
    snapshot.confidence = confidence_;
    snapshot.distance = distance_;
    snapshot.learning = learning_;
    snapshot.learning_samples = learning_count_;
    snapshot.learning_name = learning_name_;
    snapshot.last_result = last_result_;
    snapshot.profile_count = profiles_.size();
    if (include_history) {
        if (recent_event_count_ > 0) {
            const size_t last_index = (recent_event_next_ + kRecentEventCapacity - 1) %
                                      kRecentEventCapacity;
            snapshot.last_event = recent_events_[last_index];
        }
        snapshot.recent_events.reserve(recent_event_count_);
        const size_t first_index = recent_event_count_ == kRecentEventCapacity
            ? recent_event_next_ : 0;
        for (size_t index = 0; index < recent_event_count_; ++index) {
            snapshot.recent_events.push_back(
                recent_events_[(first_index + index) % kRecentEventCapacity]
            );
        }
        snapshot.profiles = profiles_;
    }

    xSemaphoreGive(mutex_);
    return snapshot;
}

std::string MagneticShellManager::DescribeCurrent() const
{
    const Snapshot snapshot = GetSnapshot();
    if (!snapshot.has_data) {
        return "底座暂时没有磁场数据";
    }
    if (snapshot.learning) {
        return "正在学习" + snapshot.learning_name + "，已采集" +
               std::to_string(snapshot.learning_samples) + "/" +
               std::to_string(kLearningSamples) + "个样本";
    }
    if (!snapshot.stable) {
        return "磁场正在变化，请保持外壳静止后再识别";
    }
    if (snapshot.recognition == RecognitionState::Matched) {
        return "当前识别为" + snapshot.matched_name + "，置信度" +
               std::to_string(snapshot.confidence) + "%";
    }
    if (snapshot.recognition == RecognitionState::NoProfiles) {
        return "还没有外壳档案，可以学习当前外壳";
    }
    return "当前磁场与已有档案不同，可能是新外壳";
}

std::string MagneticShellManager::DescribeProfiles() const
{
    const Snapshot snapshot = GetSnapshot();
    if (snapshot.profiles.empty()) {
        return "外壳档案列表为空";
    }

    std::string result = "已记录" + std::to_string(snapshot.profiles.size()) + "个外壳：";
    for (size_t index = 0; index < snapshot.profiles.size(); ++index) {
        const Profile& profile = snapshot.profiles[index];
        if (index != 0) {
            result += "；";
        }
        result += "编号" + std::to_string(profile.id) + "，" + profile.name;
    }
    return result;
}

std::string MagneticShellManager::DescribeEvents() const
{
    const Snapshot snapshot = GetSnapshot();
    if (snapshot.recent_events.empty()) {
        return "还没有磁吸识别事件";
    }

    std::string result = "最近磁吸事件：";
    for (size_t index = 0; index < snapshot.recent_events.size(); ++index) {
        const RecognitionEvent& event = snapshot.recent_events[index];
        if (index != 0) {
            result += "；";
        }
        result += "#" + std::to_string(event.sequence) + " " + event_type_name(event.type);
        if (!event.profile_name.empty()) {
            result += " " + event.profile_name;
        }
        result += " 置信度" + std::to_string(event.confidence) + "%";
    }
    return result;
}

void MagneticShellManager::LoadProfilesLocked()
{
    profiles_.clear();
    Settings settings(kSettingsNamespace, false);
    const std::string json_text = settings.GetString(kProfilesKey, "");
    if (json_text.empty()) {
        return;
    }

    cJSON* root = cJSON_Parse(json_text.c_str());
    if (!cJSON_IsArray(root)) {
        ESP_LOGW(kTag, "Ignoring invalid profile data");
        cJSON_Delete(root);
        return;
    }

    cJSON* item = nullptr;
    cJSON_ArrayForEach(item, root) {
        if (profiles_.size() >= kMaxProfiles || !cJSON_IsObject(item)) {
            break;
        }
        cJSON* id = cJSON_GetObjectItem(item, "id");
        cJSON* name = cJSON_GetObjectItem(item, "name");
        cJSON* x = cJSON_GetObjectItem(item, "x");
        cJSON* y = cJSON_GetObjectItem(item, "y");
        cJSON* z = cJSON_GetObjectItem(item, "z");
        cJSON* magnitude = cJSON_GetObjectItem(item, "mag");
        cJSON* noise = cJSON_GetObjectItem(item, "noise");
        cJSON* tolerance = cJSON_GetObjectItem(item, "tol");
        cJSON* samples = cJSON_GetObjectItem(item, "n");
        if (!cJSON_IsNumber(id) || !cJSON_IsString(name) ||
            !cJSON_IsNumber(x) || !cJSON_IsNumber(y) || !cJSON_IsNumber(z) ||
            !cJSON_IsNumber(magnitude)) {
            continue;
        }

        Profile profile;
        profile.id = id->valueint;
        profile.name = truncate_utf8(name->valuestring, kMaximumNameBytes);
        profile.x = static_cast<int16_t>(x->valueint);
        profile.y = static_cast<int16_t>(y->valueint);
        profile.z = static_cast<int16_t>(z->valueint);
        profile.magnitude = magnitude->valueint;
        profile.noise = cJSON_IsNumber(noise) ? noise->valueint : 0;
        profile.tolerance = cJSON_IsNumber(tolerance)
            ? std::clamp(tolerance->valueint, kMinimumTolerance, kMaximumTolerance)
            : kMinimumTolerance;
        profile.sample_count = cJSON_IsNumber(samples) ? samples->valueint : kLearningSamples;
        if (profile.id > 0 && !profile.name.empty()) {
            profiles_.push_back(profile);
        }
    }
    cJSON_Delete(root);
}

bool MagneticShellManager::SaveProfilesLocked()
{
    cJSON* root = cJSON_CreateArray();
    if (root == nullptr) {
        return false;
    }

    for (const Profile& profile : profiles_) {
        cJSON* item = cJSON_CreateObject();
        if (item == nullptr) {
            cJSON_Delete(root);
            return false;
        }
        cJSON_AddNumberToObject(item, "id", profile.id);
        cJSON_AddStringToObject(item, "name", profile.name.c_str());
        cJSON_AddNumberToObject(item, "x", profile.x);
        cJSON_AddNumberToObject(item, "y", profile.y);
        cJSON_AddNumberToObject(item, "z", profile.z);
        cJSON_AddNumberToObject(item, "mag", profile.magnitude);
        cJSON_AddNumberToObject(item, "noise", profile.noise);
        cJSON_AddNumberToObject(item, "tol", profile.tolerance);
        cJSON_AddNumberToObject(item, "n", profile.sample_count);
        cJSON_AddItemToArray(root, item);
    }

    char* serialized = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    if (serialized == nullptr) {
        return false;
    }

    Settings settings(kSettingsNamespace, true);
    settings.SetString(kProfilesKey, serialized);
    cJSON_free(serialized);
    return true;
}

void MagneticShellManager::UpdateRecognitionLocked()
{
    if (window_count_ == 0) {
        recognition_ = RecognitionState::NoData;
        return;
    }

    double sum_x = 0;
    double sum_y = 0;
    double sum_z = 0;
    double sum_x2 = 0;
    double sum_y2 = 0;
    double sum_z2 = 0;
    for (size_t index = 0; index < window_count_; ++index) {
        const Sample& sample = window_[index];
        sum_x += sample.x;
        sum_y += sample.y;
        sum_z += sample.z;
        sum_x2 += static_cast<double>(sample.x) * sample.x;
        sum_y2 += static_cast<double>(sample.y) * sample.y;
        sum_z2 += static_cast<double>(sample.z) * sample.z;
    }

    const double count = static_cast<double>(window_count_);
    const double mean_x = sum_x / count;
    const double mean_y = sum_y / count;
    const double mean_z = sum_z / count;
    const double variance = std::max(0.0,
        ((sum_x2 / count) - mean_x * mean_x +
         (sum_y2 / count) - mean_y * mean_y +
         (sum_z2 / count) - mean_z * mean_z) / 3.0
    );

    filtered_x_ = static_cast<int16_t>(std::lround(mean_x));
    filtered_y_ = static_cast<int16_t>(std::lround(mean_y));
    filtered_z_ = static_cast<int16_t>(std::lround(mean_z));
    filtered_magnitude_ = vector_magnitude(mean_x, mean_y, mean_z);
    noise_ = static_cast<int>(std::lround(std::sqrt(variance)));
    stable_ = window_count_ >= kRecognitionWindowMin && noise_ <= kStableNoiseLimit;
    matched_profile_id_ = 0;
    matched_name_.clear();
    confidence_ = 0;
    distance_ = 0;

    if (!stable_) {
        recognition_ = RecognitionState::Unstable;
        return;
    }
    if (profiles_.empty()) {
        recognition_ = RecognitionState::NoProfiles;
        return;
    }

    const Profile* best = nullptr;
    int best_distance = std::numeric_limits<int>::max();
    for (const Profile& profile : profiles_) {
        const int candidate = matching_distance(
            filtered_x_, filtered_y_, filtered_z_, filtered_magnitude_, profile
        );
        if (candidate < best_distance) {
            best_distance = candidate;
            best = &profile;
        }
    }

    distance_ = best_distance;
    if (best != nullptr && best_distance <= best->tolerance) {
        recognition_ = RecognitionState::Matched;
        matched_profile_id_ = best->id;
        matched_name_ = best->name;
        confidence_ = std::clamp(100 - (best_distance * 70 / best->tolerance), 30, 100);
    } else {
        recognition_ = RecognitionState::Unknown;
    }
}

void MagneticShellManager::UpdateEventStateLocked(int64_t timestamp_ms)
{
    RecognitionState event_state = RecognitionState::Unstable;
    int event_profile_id = 0;
    if (!has_data_) {
        event_state = RecognitionState::NoData;
    } else if (stable_ && recognition_ == RecognitionState::Matched) {
        event_state = RecognitionState::Matched;
        event_profile_id = matched_profile_id_;
    } else if (stable_ && recognition_ == RecognitionState::Unknown) {
        event_state = RecognitionState::Unknown;
    } else {
        // Ignore moving/no-profile states for transition purposes.
        candidate_event_count_ = 0;
        return;
    }

    if (event_state != candidate_event_state_ ||
        event_profile_id != candidate_event_profile_id_) {
        candidate_event_state_ = event_state;
        candidate_event_profile_id_ = event_profile_id;
        candidate_event_count_ = 1;
        return;
    }

    if (candidate_event_count_ < kEventConfirmationSamples) {
        ++candidate_event_count_;
    }
    if (candidate_event_count_ < kEventConfirmationSamples ||
        (timestamp_ms - last_event_ms_) < kEventCooldownMs) {
        return;
    }

    if (event_state == committed_event_state_ &&
        event_profile_id == committed_event_profile_id_) {
        return;
    }

    EventType event_type = EventType::None;
    if (event_state == RecognitionState::Matched) {
        event_type = committed_event_state_ == RecognitionState::Matched
            ? EventType::Changed : EventType::Attached;
    } else if (event_state == RecognitionState::Unknown) {
        event_type = EventType::Unknown;
    }
    if (event_type == EventType::None) {
        return;
    }

    EmitEventLocked(event_type, timestamp_ms);
    committed_event_state_ = event_state;
    committed_event_profile_id_ = event_profile_id;
    candidate_event_count_ = 0;
}

void MagneticShellManager::EmitEventLocked(EventType type, int64_t timestamp_ms)
{
    RecognitionEvent event;
    event.sequence = next_event_sequence_++;
    event.type = type;
    event.profile_id = matched_profile_id_;
    event.profile_name = matched_name_;
    event.x = filtered_x_;
    event.y = filtered_y_;
    event.z = filtered_z_;
    event.magnitude = filtered_magnitude_;
    event.confidence = confidence_;
    event.distance = distance_;
    event.timestamp_ms = timestamp_ms;

    recent_events_[recent_event_next_] = event;
    recent_event_next_ = (recent_event_next_ + 1) % kRecentEventCapacity;
    recent_event_count_ = std::min(recent_event_count_ + 1, kRecentEventCapacity);
    last_event_ms_ = timestamp_ms;
    last_result_ = std::string("磁吸事件：") + event_type_name(type);

    ESP_LOGI(kTag, "Event #%u type=%s profile=%d name=%s xyz=(%d,%d,%d) mag=%d confidence=%d distance=%d",
             event.sequence, event_type_name(event.type), event.profile_id,
             event.profile_name.c_str(), event.x, event.y, event.z,
             event.magnitude, event.confidence, event.distance);
}

void MagneticShellManager::FinishLearningLocked()
{
    const double count = static_cast<double>(learning_count_);
    const double mean_x = learning_sum_x_ / count;
    const double mean_y = learning_sum_y_ / count;
    const double mean_z = learning_sum_z_ / count;
    const double variance = std::max(0.0,
        ((learning_sum_x2_ / count) - mean_x * mean_x +
         (learning_sum_y2_ / count) - mean_y * mean_y +
         (learning_sum_z2_ / count) - mean_z * mean_z) / 3.0
    );
    const int noise = static_cast<int>(std::lround(std::sqrt(variance)));

    learning_ = false;
    if (noise > kLearningNoiseLimit) {
        last_result_ = "采样波动过大，请保持外壳不动后重试";
        ESP_LOGW(kTag, "Learning rejected: noise=%d", noise);
        return;
    }

    Profile profile;
    profile.id = NextProfileIdLocked();
    profile.name = learning_name_;
    profile.x = static_cast<int16_t>(std::lround(mean_x));
    profile.y = static_cast<int16_t>(std::lround(mean_y));
    profile.z = static_cast<int16_t>(std::lround(mean_z));
    profile.magnitude = vector_magnitude(mean_x, mean_y, mean_z);
    profile.noise = noise;
    profile.tolerance = std::clamp(100 + noise * 3, kMinimumTolerance, kMaximumTolerance);
    profile.sample_count = learning_count_;

    auto same_name = std::find_if(profiles_.begin(), profiles_.end(),
                                  [&profile](const Profile& existing) {
                                      return existing.name == profile.name;
                                  });
    if (same_name != profiles_.end()) {
        profile.id = same_name->id;
        *same_name = profile;
        last_result_ = "已更新外壳档案：" + profile.name;
    } else {
        profiles_.push_back(profile);
        last_result_ = "已记录外壳：" + profile.name;
    }

    if (!SaveProfilesLocked()) {
        last_result_ = "外壳采样完成，但保存失败";
    }
    ESP_LOGI(kTag, "Profile id=%d name=%s xyz=(%d,%d,%d) mag=%d noise=%d tol=%d",
             profile.id, profile.name.c_str(), profile.x, profile.y, profile.z,
             profile.magnitude, profile.noise, profile.tolerance);
}

std::string MagneticShellManager::NormalizeNameLocked(const std::string& requested_name) const
{
    if (!requested_name.empty()) {
        return truncate_utf8(requested_name, kMaximumNameBytes);
    }
    return "外壳 " + std::to_string(NextProfileIdLocked());
}

int MagneticShellManager::NextProfileIdLocked() const
{
    for (int candidate = 1; candidate <= static_cast<int>(kMaxProfiles); ++candidate) {
        const bool used = std::any_of(profiles_.begin(), profiles_.end(),
                                      [candidate](const Profile& profile) {
                                          return profile.id == candidate;
                                      });
        if (!used) {
            return candidate;
        }
    }
    return static_cast<int>(profiles_.size()) + 1;
}
