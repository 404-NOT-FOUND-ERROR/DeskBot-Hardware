from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from protocol.magnetic_recognition import (
    MagneticRecognizer,
    RecognitionConfig,
    STATUS_AMBIGUOUS,
    STATUS_KNOWN,
    STATUS_MISSING,
    STATUS_UNKNOWN,
    STATUS_UNSTABLE,
    STATUS_WARMING_UP,
)
from protocol.magnetic_telemetry import decode_payload


UTC = timezone.utc


def sample(kind: int, xyz: tuple[int, int, int], second: float):
    timestamp = datetime(2026, 10, 16, 12, 0, 0, tzinfo=UTC) + timedelta(seconds=second)
    payload = bytes((0x06, kind)) + b"".join(
        value.to_bytes(2, "big", signed=True) for value in xyz
    )
    return decode_payload(payload, host_received_at=timestamp)


def feed_window(
    recognizer: MagneticRecognizer,
    xyz: tuple[int, int, int] = (190, -540, -940),
    *,
    start: float = 0.0,
    kind: int = 1,
):
    result = None
    for index in range(recognizer.config.window_size):
        result = recognizer.feed(sample(kind, xyz, start + index * 0.1))
    assert result is not None
    return result


class MagneticRecognitionTests(unittest.TestCase):
    def config(self, **overrides):
        values = dict(
            window_size=4,
            min_window_span_s=0.2,
            max_gap_s=0.5,
            max_age_s=1.0,
            stable_std_max=5.0,
            stable_range_max=12.0,
            match_radius=30.0,
            min_margin=0.15,
            confirmations_required=2,
            max_profiles=3,
        )
        values.update(overrides)
        return RecognitionConfig(**values)

    def test_warmup_stable_manual_register_and_known_confirmation(self):
        recognizer = MagneticRecognizer(self.config())
        stable = feed_window(recognizer)
        self.assertEqual(stable.status, STATUS_UNKNOWN)
        profile = recognizer.register("上端外壳", as_of="2026-10-16T12:00:00.500Z")
        self.assertEqual(profile.label, "上端外壳")
        self.assertEqual(profile.kind, 1)
        pending = recognizer.feed(sample(1, (191, -539, -941), 0.6))
        self.assertEqual(pending.status, STATUS_WARMING_UP)
        confirmed = recognizer.feed(sample(1, (190, -540, -940), 0.7))
        self.assertEqual(confirmed.status, STATUS_KNOWN)
        self.assertEqual(confirmed.label, "上端外壳")
        self.assertIsNotNone(confirmed.similarity)
        self.assertLessEqual(confirmed.similarity, 1.0)

    def test_far_signature_is_unknown_and_never_auto_registered(self):
        recognizer = MagneticRecognizer(self.config())
        feed_window(recognizer)
        recognizer.register("上端", as_of="2026-10-16T12:00:00.500Z")
        result = feed_window(recognizer, xyz=(500, 500, 500), start=1.0)
        self.assertEqual(result.status, STATUS_UNKNOWN)
        self.assertEqual(len(recognizer.profiles), 1)

    def test_two_close_profiles_are_ambiguous_until_margin_is_clear(self):
        recognizer = MagneticRecognizer(self.config(min_margin=0.20))
        feed_window(recognizer, xyz=(0, 0, 0))
        recognizer.register("A", as_of="2026-10-16T12:00:00.500Z")
        feed_window(recognizer, xyz=(10, 0, 0), start=1.0)
        recognizer.register("B", as_of="2026-10-16T12:00:01.500Z")
        result = feed_window(recognizer, xyz=(5, 0, 0), start=2.0)
        self.assertEqual(result.status, STATUS_AMBIGUOUS)
        self.assertIsNotNone(result.runner_up_distance)
        self.assertIsNotNone(result.margin)
        self.assertLess(result.margin, 0.20)

    def test_unstable_motion_does_not_match(self):
        recognizer = MagneticRecognizer(
            self.config(stable_std_max=2.0, stable_range_max=4.0)
        )
        outputs = []
        for index, value in enumerate((0, 20, -20, 30)):
            outputs.append(recognizer.feed(sample(1, (value, 0, 0), index * 0.1)))
        self.assertEqual(outputs[-1].status, STATUS_UNSTABLE)
        self.assertIsNone(outputs[-1].candidate_id)

    def test_gap_and_non_monotonic_timestamp_reset_window(self):
        recognizer = MagneticRecognizer(self.config())
        feed_window(recognizer)
        gap = recognizer.feed(sample(1, (190, -540, -940), 3.0))
        self.assertEqual(gap.status, STATUS_WARMING_UP)
        self.assertEqual(gap.reason, "timestamp_gap_reset")
        old = recognizer.feed(sample(1, (190, -540, -940), 2.5))
        self.assertEqual(old.status, STATUS_WARMING_UP)
        self.assertEqual(old.reason, "timestamp_non_monotonic_reset")
        self.assertEqual(len(recognizer.window), 0)
        older_again = recognizer.feed(sample(1, (190, -540, -940), 2.6))
        self.assertEqual(older_again.reason, "timestamp_non_monotonic_reset")
        self.assertEqual(len(recognizer.window), 0)

    def test_window_drops_unbounded_raw_log_line(self):
        recognizer = MagneticRecognizer(self.config())
        source = replace(
            sample(1, (1, 2, 3), 0.0),
            kind_name="K" * 1_000_000,
            units="U" * 1_000_000,
            payload_hex="P" * 1_000_000,
            frame_hex="F" * 1_000_000,
            source="S" * 1_000_000,
            host_time_source="T" * 1_000_000,
            raw_line="x" * 1_000_000,
        )
        recognizer.feed(source)
        compact = recognizer.window[0]
        self.assertEqual(compact.kind_name, "BMM150")
        self.assertEqual(compact.units, "transport_counts")
        self.assertEqual(compact.payload_hex, "")
        self.assertEqual(compact.frame_hex, "")
        self.assertEqual(compact.source, "recognition_window")
        self.assertEqual(compact.host_time_source, "recognizer")
        self.assertIsNone(compact.raw_line)
        self.assertLessEqual(len(compact.host_received_at or ""), 40)

    def test_stale_tick_is_missing_and_does_not_infer_removal(self):
        recognizer = MagneticRecognizer(self.config())
        feed_window(recognizer)
        recognizer.register("A", as_of="2026-10-16T12:00:00.500Z")
        missing = recognizer.tick("2026-10-16T12:00:10Z")
        self.assertEqual(missing.status, STATUS_MISSING)
        self.assertEqual(len(recognizer.profiles), 1)
        self.assertEqual(recognizer.remove_profile("does-not-exist"), False)

    def test_reset_stream_preserves_profiles_but_clears_watermark_and_result(self):
        recognizer = MagneticRecognizer(self.config())
        feed_window(recognizer)
        recognizer.register("A", as_of="2026-10-16T12:00:00.300Z")
        self.assertNotEqual(recognizer.last_result.reason, "no_sample")
        recognizer.reset_stream()
        self.assertEqual(recognizer.last_result.status, STATUS_MISSING)
        self.assertEqual(recognizer.last_result.reason, "no_sample")
        self.assertEqual(len(recognizer.window), 0)
        self.assertEqual(len(recognizer.profiles), 1)
        # A separate recording can legitimately start at an earlier clock.
        result = recognizer.feed(sample(1, (190, -540, -940), -100.0))
        self.assertEqual(result.status, STATUS_WARMING_UP)
        self.assertEqual(len(recognizer.window), 1)

    def test_registration_requires_explicit_nonempty_as_of_and_resets_old_candidate(self):
        recognizer = MagneticRecognizer(self.config())
        feed_window(recognizer)
        with self.assertRaises(ValueError):
            recognizer.register("missing", as_of=None)
        with self.assertRaises(ValueError):
            recognizer.register("empty", as_of="")
        first = recognizer.register("A", as_of="2026-10-16T12:00:00.300Z")
        recognizer.feed(sample(1, (190, -540, -940), 0.4))
        recognizer.feed(sample(1, (190, -540, -940), 0.5))
        self.assertEqual(recognizer.last_result.status, STATUS_KNOWN)
        second = recognizer.register("B", as_of="2026-10-16T12:00:00.500Z")
        self.assertNotEqual(first.profile_id, second.profile_id)
        self.assertNotEqual(recognizer.last_result.status, STATUS_KNOWN)
        self.assertEqual(recognizer.last_result.confirmation_count, 0)

    def test_synthetic_new_competing_profile_requires_new_confirmation_sequence(self):
        recognizer = MagneticRecognizer(self.config(confirmations_required=3))
        feed_window(recognizer, xyz=(0, 0, 0))
        first = recognizer.register("A", as_of="2026-10-16T12:00:00.300Z")
        for index in range(4, 7):
            recognizer.feed(sample(1, (0, 0, 0), index * 0.1))
        self.assertEqual(recognizer.last_result.profile_id, first.profile_id)
        shifted = feed_window(recognizer, xyz=(10, 0, 0), start=0.7)
        self.assertEqual(shifted.status, STATUS_KNOWN)
        second = recognizer.register("nearby-B", as_of="2026-10-16T12:00:01Z")
        self.assertEqual(recognizer.last_result.status, STATUS_WARMING_UP)
        self.assertEqual(recognizer.last_result.candidate_id, second.profile_id)
        self.assertEqual(recognizer.last_result.confirmation_count, 0)
        for index in (11, 12):
            pending = recognizer.feed(sample(1, (10, 0, 0), index * 0.1))
            self.assertEqual(pending.status, STATUS_WARMING_UP)
            self.assertIsNone(pending.profile_id)
        confirmed = recognizer.feed(sample(1, (10, 0, 0), 1.3))
        self.assertEqual(confirmed.status, STATUS_KNOWN)
        self.assertEqual(confirmed.profile_id, second.profile_id)

    def test_remove_profile_clears_stale_known_result_immediately(self):
        recognizer = MagneticRecognizer(self.config())
        feed_window(recognizer)
        profile = recognizer.register("A", as_of="2026-10-16T12:00:00.300Z")
        recognizer.feed(sample(1, (190, -540, -940), 0.4))
        known = recognizer.feed(sample(1, (190, -540, -940), 0.5))
        self.assertEqual(known.status, STATUS_KNOWN)
        self.assertTrue(recognizer.remove_profile(profile.profile_id))
        self.assertNotEqual(recognizer.last_result.status, STATUS_KNOWN)
        self.assertEqual(recognizer.last_result.reason, "profile_removed")
        self.assertIsNone(recognizer.last_result.profile_id)

    def test_sensor_kind_isolation_and_unknown_sensor(self):
        recognizer = MagneticRecognizer(self.config())
        feed_window(recognizer, kind=1)
        recognizer.register("BMM", as_of="2026-10-16T12:00:00.500Z")
        switched = recognizer.feed(sample(2, (190, -540, -940), 1.0))
        self.assertEqual(switched.status, STATUS_WARMING_UP)
        self.assertEqual(switched.reason, "sensor_changed_reset")
        self.assertIsNone(switched.candidate_id)
        unknown = recognizer.feed(sample(3, (1, 2, 3), 1.1))
        self.assertEqual(unknown.status, STATUS_UNKNOWN)
        self.assertEqual(unknown.reason, "unsupported_sensor")
        self.assertEqual(len(recognizer.profiles), 1)

    def test_bounded_window_and_profile_limit(self):
        recognizer = MagneticRecognizer(self.config(max_profiles=1))
        for index in range(20):
            recognizer.feed(sample(1, (1, 2, 3), index * 0.1))
        self.assertEqual(len(recognizer.window), 4)
        feed_window(recognizer, xyz=(1, 2, 3), start=2.1)
        recognizer.register("only", as_of="2026-10-16T12:00:02.500Z")
        with self.assertRaises(ValueError):
            feed_window(recognizer, xyz=(100, 100, 100), start=3.0)
            recognizer.register("second", as_of="2026-10-16T12:00:03.500Z")

    def test_synthetic_repeated_queries_do_not_confirm_without_new_data(self):
        recognizer = MagneticRecognizer(self.config(confirmations_required=3))
        feed_window(recognizer)
        recognizer.register("position-A", as_of="2026-10-16T12:00:00.300Z")
        pending = recognizer.feed(sample(1, (190, -540, -940), 0.4))
        self.assertEqual(pending.confirmation_count, 1)
        for _ in range(100):
            repeated = recognizer.tick("2026-10-16T12:00:00.500Z")
            self.assertEqual(repeated.status, STATUS_WARMING_UP)
            self.assertEqual(repeated.confirmation_count, 1)
        recognizer.feed(sample(1, (190, -540, -940), 0.5))
        known = recognizer.feed(sample(1, (190, -540, -940), 0.6))
        self.assertEqual(known.status, STATUS_KNOWN)
        for index in range(7, 60):
            result = recognizer.feed(sample(1, (190, -540, -940), index * 0.1))
            self.assertLessEqual(result.confirmation_count, 3)

    def test_synthetic_drift_and_motion_clear_previously_known_identity(self):
        recognizer = MagneticRecognizer(self.config())
        feed_window(recognizer, xyz=(0, 0, 0))
        recognizer.register("origin", as_of="2026-10-16T12:00:00.300Z")
        recognizer.feed(sample(1, (0, 0, 0), 0.4))
        known = recognizer.feed(sample(1, (0, 0, 0), 0.5))
        self.assertEqual(known.status, STATUS_KNOWN)
        for index, value in enumerate((20, 40, 60, 80), start=6):
            moving = recognizer.feed(sample(1, (value, 0, 0), index * 0.1))
            self.assertEqual(moving.status, STATUS_UNSTABLE)
            self.assertIsNone(moving.profile_id)
            self.assertEqual(moving.confirmation_count, 0)
        far = feed_window(recognizer, xyz=(100, 0, 0), start=1.0)
        self.assertEqual(far.status, STATUS_UNKNOWN)
        self.assertEqual(len(recognizer.profiles), 1)

    def test_synthetic_same_xyz_on_second_sensor_cannot_use_first_sensor_profile(self):
        recognizer = MagneticRecognizer(self.config())
        feed_window(recognizer, xyz=(0, 0, 0), kind=1)
        first = recognizer.register("BMM150-position", as_of="2026-10-16T12:00:00.300Z")
        second_sensor = feed_window(recognizer, xyz=(0, 0, 0), kind=2, start=0.4)
        self.assertEqual(second_sensor.status, STATUS_UNKNOWN)
        self.assertEqual(second_sensor.reason, "no_profile_for_sensor")
        self.assertIsNone(second_sensor.nearest_distance)
        second = recognizer.register("QMC6309-position", as_of="2026-10-16T12:00:00.700Z")
        self.assertNotEqual(first.profile_id, second.profile_id)
        recognizer.feed(sample(2, (0, 0, 0), 0.8))
        known = recognizer.feed(sample(2, (0, 0, 0), 0.9))
        self.assertEqual(known.profile_id, second.profile_id)

    def test_synthetic_future_stale_missing_and_invalid_samples_reset(self):
        recognizer = MagneticRecognizer(self.config())
        good = sample(1, (0, 0, 0), 0.0)
        for bad in (
            replace(good, host_received_at=None),
            replace(good, host_received_at="bad timestamp"),
            replace(good, host_received_at="2026-10-16T12:00:00"),
            replace(good, x=float("nan")),
            replace(good, y=float("inf")),
            replace(good, z=32768),
        ):
            result = recognizer.feed(bad)
            self.assertEqual(result.status, STATUS_MISSING)
            self.assertEqual(len(recognizer.window), 0)
        future = recognizer.feed(good, as_of="2026-10-16T11:59:59Z")
        self.assertEqual(future.reason, "future_sample")
        stale = recognizer.feed(good, as_of="2026-10-16T12:00:01.001Z")
        self.assertEqual(stale.reason, "sample_stale")
        boundary = recognizer.feed(good, as_of="2026-10-16T12:00:01Z")
        self.assertEqual(boundary.status, STATUS_WARMING_UP)

    def test_synthetic_reconnect_after_missing_requires_full_new_window(self):
        recognizer = MagneticRecognizer(self.config())
        feed_window(recognizer)
        recognizer.register("A", as_of="2026-10-16T12:00:00.300Z")
        recognizer.feed(sample(1, (190, -540, -940), 0.4))
        self.assertEqual(recognizer.feed(sample(1, (190, -540, -940), 0.5)).status, STATUS_KNOWN)
        missing = recognizer.tick("2026-10-16T12:00:01.501Z")
        self.assertEqual(missing.status, STATUS_MISSING)
        for index in range(3):
            warming = recognizer.feed(sample(1, (190, -540, -940), 2.0 + index * 0.1))
            self.assertEqual(warming.status, STATUS_WARMING_UP)
            self.assertIsNone(warming.profile_id)
        pending = recognizer.feed(sample(1, (190, -540, -940), 2.3))
        self.assertEqual(pending.confirmation_count, 1)
        self.assertEqual(recognizer.feed(sample(1, (190, -540, -940), 2.4)).status, STATUS_KNOWN)

    def test_synthetic_short_span_registration_and_explicit_removal_rules(self):
        recognizer = MagneticRecognizer(self.config(min_window_span_s=0.5))
        result = feed_window(recognizer)
        self.assertEqual(result.reason, "window_span_short")
        with self.assertRaises(ValueError):
            recognizer.register("too-fast", as_of="2026-10-16T12:00:00.300Z")
        for index in range(4):
            recognizer.feed(sample(1, (190, -540, -940), 0.5 + index * 0.2))
        profile = recognizer.register("manual-label", as_of="2026-10-16T12:00:01.100Z", profile_id="shell-1")
        self.assertEqual(profile.profile_id, "shell-1")
        with self.assertRaises(ValueError):
            recognizer.register("duplicate-id", as_of="2026-10-16T12:00:01.100Z", profile_id="shell-1")
        recognizer.tick("2026-10-16T12:00:20Z")
        self.assertEqual(len(recognizer.profiles), 1)
        self.assertTrue(recognizer.remove_profile("shell-1"))
        self.assertEqual(len(recognizer.profiles), 0)
        with self.assertRaises(ValueError):
            recognizer.register("missing", as_of="2026-10-16T12:00:20Z")

    def test_synthetic_configuration_and_string_limits_are_bounded(self):
        for invalid in (
            {"window_size": 1},
            {"window_size": 4097},
            {"window_size": True},
            {"max_profiles": 257},
            {"max_profiles": False},
            {"confirmations_required": 0},
            {"stable_std_max": float("nan")},
            {"stable_range_max": float("inf")},
            {"max_age_s": -1},
            {"min_margin": 1.01},
            {"match_radius": 0},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.config(**invalid)
        recognizer = MagneticRecognizer(self.config())
        feed_window(recognizer)
        for label in ("", " ", "x" * 129):
            with self.subTest(label=label), self.assertRaises(ValueError):
                recognizer.register(label, as_of="2026-10-16T12:00:00.300Z")
        with self.assertRaises(ValueError):
            recognizer.register("A", as_of="2026-10-16T12:00:00.300Z", profile_id="x" * 65)

    def test_synthetic_timestamp_duplicate_does_not_create_new_confirmation(self):
        recognizer = MagneticRecognizer(self.config())
        feed_window(recognizer)
        recognizer.register("A", as_of="2026-10-16T12:00:00.300Z")
        recognizer.feed(sample(1, (190, -540, -940), 0.4))
        duplicate = recognizer.feed(sample(1, (190, -540, -940), 0.4))
        self.assertEqual(duplicate.reason, "timestamp_non_monotonic_reset")
        self.assertIsNone(duplicate.profile_id)
        self.assertEqual(duplicate.confirmation_count, 0)
        repeated = recognizer.feed(sample(1, (190, -540, -940), 0.4))
        self.assertEqual(repeated.reason, "timestamp_non_monotonic_reset")
        self.assertEqual(repeated.confirmation_count, 0)
        boundary = recognizer.feed(sample(1, (-32768, 32767, -32768), 0.5))
        self.assertEqual(boundary.status, STATUS_WARMING_UP)


if __name__ == "__main__":
    unittest.main()
