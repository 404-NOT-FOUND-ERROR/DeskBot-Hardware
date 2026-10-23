#include "base_magnetic_recognition.h"

#include <cassert>
#include <cstdint>
#include <cstring>

namespace {

BaseMagneticSample Sample(int16_t x, int16_t y, int16_t z,
                          BaseMagneticSensorKind kind = BaseMagneticSensorKind::Bmm150)
{
    BaseMagneticSample sample;
    sample.valid = true;
    sample.sensor_kind = kind;
    sample.x = x;
    sample.y = y;
    sample.z = z;
    return sample;
}

void FillStable(BaseMagneticRecognition& recognition, int64_t start_ms,
                int16_t x = 190, int16_t y = -540, int16_t z = -940,
                BaseMagneticSensorKind kind = BaseMagneticSensorKind::Bmm150)
{
    for (int index = 0; index < 10; ++index) {
        assert(recognition.AddSample(Sample(x, y, z, kind),
                                     start_ms + index * 100));
    }
}

void TestStableAndRegistration()
{
    BaseMagneticRecognition recognition;
    FillStable(recognition, 0);
    BaseMagneticRecognition::Snapshot snapshot = recognition.GetSnapshot(900);
    assert(snapshot.status == BaseMagneticRecognition::Status::NoProfiles);
    assert(snapshot.stable);
    assert(snapshot.window_count == 10);
    assert(snapshot.span_ms == 900);
    assert(snapshot.noise == 0);
    assert(snapshot.x == 190 && snapshot.y == -540 && snapshot.z == -940);

    assert(recognition.RegisterCurrent(1, "上端外壳", 900) ==
           BaseMagneticRecognition::RegistrationResult::Registered);
    snapshot = recognition.GetSnapshot(900);
    assert(snapshot.status == BaseMagneticRecognition::Status::Known);
    assert(snapshot.matched_profile_id == 1);
    assert(snapshot.confidence == 100);
    assert(snapshot.distance == 0);

    BaseMagneticRecognition::Profile profiles[2];
    assert(recognition.CopyProfiles(profiles, 2) == 1);
    assert(std::strcmp(profiles[0].name, "上端外壳") == 0);
    assert(profiles[0].sensor_kind == BaseMagneticSensorKind::Bmm150);
}

void TestUnknownAndRotationSeparation()
{
    BaseMagneticRecognition recognition;
    FillStable(recognition, 0, 190, -540, -940);
    assert(recognition.RegisterCurrent(1, "位置 A", 900) ==
           BaseMagneticRecognition::RegistrationResult::Registered);

    recognition.ResetSamples();
    FillStable(recognition, 0, -190, -540, -940);
    BaseMagneticRecognition::Snapshot snapshot = recognition.GetSnapshot(900);
    assert(snapshot.status == BaseMagneticRecognition::Status::Unknown);
    assert(snapshot.matched_profile_id == 0);
    assert(snapshot.confidence == 0);
}

void TestAmbiguousProfiles()
{
    BaseMagneticRecognition recognition;
    FillStable(recognition, 0, 190, -540, -940);
    assert(recognition.RegisterCurrent(1, "位置 A", 900) ==
           BaseMagneticRecognition::RegistrationResult::Registered);
    recognition.ResetSamples();
    FillStable(recognition, 0, 198, -540, -940);
    assert(recognition.RegisterCurrent(2, "位置 B", 900) ==
           BaseMagneticRecognition::RegistrationResult::Registered);
    recognition.ResetSamples();
    FillStable(recognition, 0, 194, -540, -940);
    const BaseMagneticRecognition::Snapshot snapshot = recognition.GetSnapshot(900);
    assert(snapshot.status == BaseMagneticRecognition::Status::Ambiguous);
    assert(snapshot.matched_profile_id == 0);
    assert(snapshot.best_profile_id != 0);
    assert(snapshot.second_distance >= snapshot.distance);
}

void TestCollectingUnstableAndStale()
{
    BaseMagneticRecognition recognition;
    for (int index = 0; index < 5; ++index) {
        assert(recognition.AddSample(Sample(190, -540, -940), index * 100));
    }
    assert(recognition.GetSnapshot(400).status ==
           BaseMagneticRecognition::Status::Collecting);

    recognition.ResetSamples();
    for (int index = 0; index < 10; ++index) {
        const int16_t x = index % 2 == 0 ? 90 : 290;
        assert(recognition.AddSample(Sample(x, -540, -940), index * 100));
    }
    assert(recognition.GetSnapshot(900).status ==
           BaseMagneticRecognition::Status::Unstable);
    assert(recognition.GetSnapshot(2501).status ==
           BaseMagneticRecognition::Status::Stale);
}

void TestGapBackwardsAndSensorIsolation()
{
    BaseMagneticRecognition recognition;
    FillStable(recognition, 0);
    assert(recognition.GetSnapshot(900).window_count == 10);
    assert(recognition.AddSample(Sample(190, -540, -940), 1501));
    assert(recognition.GetSnapshot(1501).status ==
           BaseMagneticRecognition::Status::Collecting);
    assert(recognition.GetSnapshot(1501).window_count == 1);

    recognition.ResetSamples();
    FillStable(recognition, 1000);
    assert(recognition.AddSample(Sample(190, -540, -940), 900));
    assert(recognition.GetSnapshot(900).window_count == 1);

    recognition.ResetSamples();
    FillStable(recognition, 2000);
    assert(recognition.AddSample(Sample(190, -540, -940,
                                      BaseMagneticSensorKind::Qmc6309), 2900));
    BaseMagneticRecognition::Snapshot snapshot = recognition.GetSnapshot(2900);
    assert(snapshot.window_count == 1);
    assert(snapshot.sensor_kind == BaseMagneticSensorKind::Qmc6309);
}

void TestUnsupportedAndRegistryLimits()
{
    BaseMagneticRecognition recognition;
    assert(!recognition.AddSample(Sample(1, 2, 3,
                                         BaseMagneticSensorKind::Unknown), 0));
    assert(recognition.GetSnapshot(0).status ==
           BaseMagneticRecognition::Status::UnsupportedSensor);
    assert(recognition.RegisterCurrent(1, "unknown", 0) ==
           BaseMagneticRecognition::RegistrationResult::UnsupportedSensor);
    assert(!recognition.AddSample(Sample(1, 2, 3,
                                         BaseMagneticSensorKind::Ss49e), 100));
    assert(recognition.GetSnapshot(100).status ==
           BaseMagneticRecognition::Status::UnsupportedSensor);
    assert(recognition.RegisterCurrent(1, "one axis", 100) ==
           BaseMagneticRecognition::RegistrationResult::UnsupportedSensor);

    recognition.ResetSamples();
    FillStable(recognition, 0);
    assert(recognition.RegisterCurrent(0, "bad", 900) ==
           BaseMagneticRecognition::RegistrationResult::InvalidId);
    assert(recognition.RegisterCurrent(1, "", 900) ==
           BaseMagneticRecognition::RegistrationResult::InvalidName);
    assert(recognition.RegisterCurrent(1, "bad\nname", 900) ==
           BaseMagneticRecognition::RegistrationResult::InvalidName);
    const char invalid_utf8[] = {static_cast<char>(0xc0), static_cast<char>(0x80), 0};
    assert(recognition.RegisterCurrent(1, invalid_utf8, 900) ==
           BaseMagneticRecognition::RegistrationResult::InvalidName);
    const char truncated_utf8[] = {static_cast<char>(0xe4), static_cast<char>(0xb8), 0};
    assert(recognition.RegisterCurrent(1, truncated_utf8, 900) ==
           BaseMagneticRecognition::RegistrationResult::InvalidName);
    const char utf8_surrogate[] = {static_cast<char>(0xed), static_cast<char>(0xa0),
                                  static_cast<char>(0x80), 0};
    assert(recognition.RegisterCurrent(1, utf8_surrogate, 900) ==
           BaseMagneticRecognition::RegistrationResult::InvalidName);
    assert(recognition.RegisterCurrent(1, "   ", 900) ==
           BaseMagneticRecognition::RegistrationResult::InvalidName);
    char too_long[BaseMagneticRecognition::kMaxNameBytes + 2]{};
    for (std::size_t index = 0; index < sizeof(too_long) - 1; ++index) {
        too_long[index] = 'x';
    }
    assert(recognition.RegisterCurrent(1, too_long, 900) ==
           BaseMagneticRecognition::RegistrationResult::InvalidName);

    for (uint8_t id = 1; id <= BaseMagneticRecognition::kMaxProfiles; ++id) {
        assert(recognition.RegisterCurrent(id, "profile", 900) ==
               BaseMagneticRecognition::RegistrationResult::Registered);
    }
    assert(recognition.ProfileCount() == BaseMagneticRecognition::kMaxProfiles);
    assert(recognition.RegisterCurrent(1, "replacement", 900) ==
           BaseMagneticRecognition::RegistrationResult::Replaced);
    assert(recognition.ProfileCount() == BaseMagneticRecognition::kMaxProfiles);
    assert(recognition.RegisterCurrent(7, "overflow", 900) ==
           BaseMagneticRecognition::RegistrationResult::InvalidId);
    assert(recognition.DeleteProfile(3));
    assert(recognition.ProfileCount() == BaseMagneticRecognition::kMaxProfiles - 1);
    assert(!recognition.DeleteProfile(3));
}

void TestSensorScopedMatchingAndRegistrationFreshness()
{
    BaseMagneticRecognition recognition;
    FillStable(recognition, 0);
    assert(recognition.RegisterCurrent(1, "BMM150 profile", 900) ==
           BaseMagneticRecognition::RegistrationResult::Registered);
    assert(recognition.RegisterCurrent(2, "stale profile", 2401) ==
           BaseMagneticRecognition::RegistrationResult::Stale);
    assert(recognition.RegisterCurrent(2, "future profile", 899) ==
           BaseMagneticRecognition::RegistrationResult::Stale);
    recognition.ResetSamples();
    FillStable(recognition, 0, 190, -540, -940, BaseMagneticSensorKind::Qmc6309);
    BaseMagneticRecognition::Snapshot snapshot = recognition.GetSnapshot(900);
    assert(snapshot.status == BaseMagneticRecognition::Status::Unknown);
    assert(snapshot.best_profile_id == 0);
    assert(recognition.RegisterCurrent(2, "QMC6309 profile", 900) ==
           BaseMagneticRecognition::RegistrationResult::Registered);
    snapshot = recognition.GetSnapshot(900);
    assert(snapshot.status == BaseMagneticRecognition::Status::Known);
    assert(snapshot.matched_profile_id == 2);
}

void TestWindowBoundAndTimeSpan()
{
    BaseMagneticRecognition recognition;
    for (int index = 0; index < 100; ++index) {
        assert(recognition.AddSample(Sample(190, -540, -940), index * 100));
    }
    BaseMagneticRecognition::Snapshot snapshot = recognition.GetSnapshot(9900);
    assert(snapshot.window_count == BaseMagneticRecognition::kWindowCapacity);
    assert(snapshot.first_sample_ms == 8000);
    assert(snapshot.last_sample_ms == 9900);
    assert(snapshot.span_ms == 1900);
    const BaseMagneticRecognition::Snapshot queried = recognition.GetSnapshot(9900);
    assert(queried.window_count == snapshot.window_count);
    assert(queried.profile_count == snapshot.profile_count);

    recognition.ResetSamples();
    for (int index = 0; index < 20; ++index) {
        assert(recognition.AddSample(Sample(190, -540, -940), 1000));
    }
    assert(recognition.GetSnapshot(1000).status ==
           BaseMagneticRecognition::Status::Collecting);
    assert(recognition.GetSnapshot(1000).span_ms == 0);
}

void TestLowConfidenceAndNumericLimits()
{
    BaseMagneticRecognition recognition;
    FillStable(recognition, 0, 0, 0, 0);
    assert(recognition.RegisterCurrent(1, "zero profile", 900) ==
           BaseMagneticRecognition::RegistrationResult::Registered);
    recognition.ResetSamples();
    FillStable(recognition, 0, 100, 0, 0);
    BaseMagneticRecognition::Snapshot snapshot = recognition.GetSnapshot(900);
    assert(snapshot.status == BaseMagneticRecognition::Status::Ambiguous);
    assert(snapshot.confidence < BaseMagneticRecognition::kMinimumKnownConfidence);
    assert(snapshot.matched_profile_id == 0);

    recognition.ResetSamples();
    FillStable(recognition, 0, -32768, 32767, -32768);
    snapshot = recognition.GetSnapshot(900);
    assert(snapshot.x == -32768 && snapshot.y == 32767 && snapshot.z == -32768);
    assert(snapshot.magnitude > 32767);
    assert(snapshot.noise == 0);
    assert(snapshot.status == BaseMagneticRecognition::Status::Unknown);

    const BaseMagneticSample invalid;
    assert(!recognition.AddSample(invalid, 1000));
    assert(recognition.GetSnapshot(1000).status ==
           BaseMagneticRecognition::Status::NoData);
    assert(!recognition.AddSample(Sample(1, 2, 3), -1));
    assert(recognition.GetSnapshot(1000).status ==
           BaseMagneticRecognition::Status::NoData);
}

} // namespace

int main()
{
    TestStableAndRegistration();
    TestUnknownAndRotationSeparation();
    TestAmbiguousProfiles();
    TestCollectingUnstableAndStale();
    TestGapBackwardsAndSensorIsolation();
    TestUnsupportedAndRegistryLimits();
    TestSensorScopedMatchingAndRegistrationFreshness();
    TestWindowBoundAndTimeSpan();
    TestLowConfidenceAndNumericLimits();
    return 0;
}