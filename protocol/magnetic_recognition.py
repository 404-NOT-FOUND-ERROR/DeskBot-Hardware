"""Deterministic, host-side magnetic shell recognition (M2a).

This module deliberately operates on :class:`MagneticSample` objects that have
already been decoded by ``protocol.magnetic_telemetry``.  It owns no UART,
firmware, NVS, UI, or role/personality state.  Profiles are RAM-only and are
created only by an explicit call to :meth:`MagneticRecognizer.register`.

The recognizer is intentionally conservative: one sensor kind has one bounded
sample window, timestamps must move forward, gaps reset that window, and a
candidate must be observed repeatedly before it is reported as ``known``.
``similarity`` is a unitless distance score, not a probability.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
from typing import Deque, Iterable

from .magnetic_telemetry import KIND_NAMES, MagneticSample


STATUS_WARMING_UP = "warming_up"
STATUS_UNSTABLE = "unstable"
STATUS_UNKNOWN = "unknown"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_KNOWN = "known"
STATUS_MISSING = "missing"
SUPPORTED_KINDS = frozenset((1, 2))  # BMM150/QMC6309 have three XYZ values


def _number(value: object, name: str) -> float:
    """Return a finite float, rejecting bool and non-numeric values."""

    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _timestamp(value: str | datetime | None, *, name: str) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{name} must be ISO-8601") from exc
    else:
        raise ValueError(f"{name} must be a datetime, ISO-8601 string, or None")
    if result.tzinfo is None:
        raise ValueError(f"{name} requires an explicit timezone")
    return result.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _mean(values: Iterable[float]) -> float:
    values = tuple(values)
    return sum(values) / len(values)


def _stddev(values: Iterable[float]) -> float:
    values = tuple(values)
    mean = _mean(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def _vector_distance(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


@dataclass(frozen=True)
class RecognitionConfig:
    """Bounded and explicit knobs for the RAM recognizer.

    The defaults are deliberately suitable for offline experiments, not a
    claim of calibrated product thresholds.  Each field is validated so a
    caller cannot accidentally turn the bounded window into an unbounded one.
    """

    window_size: int = 8
    min_window_span_s: float = 0.3
    max_profiles: int = 16
    max_gap_s: float = 2.0
    max_age_s: float = 2.0
    stable_std_max: float = 12.0
    stable_range_max: float = 40.0
    match_radius: float = 80.0
    min_margin: float = 0.15
    confirmations_required: int = 3

    def __post_init__(self) -> None:
        if isinstance(self.window_size, bool) or not isinstance(self.window_size, int) or not 2 <= self.window_size <= 4096:
            raise ValueError("window_size must be an integer from 2 through 4096")
        if isinstance(self.max_profiles, bool) or not isinstance(self.max_profiles, int) or not 1 <= self.max_profiles <= 256:
            raise ValueError("max_profiles must be an integer from 1 through 256")
        if isinstance(self.confirmations_required, bool) or not isinstance(self.confirmations_required, int) or not 1 <= self.confirmations_required <= 4096:
            raise ValueError("confirmations_required must be an integer from 1 through 4096")
        for name in (
            "min_window_span_s",
            "max_gap_s",
            "max_age_s",
            "stable_std_max",
            "stable_range_max",
            "match_radius",
            "min_margin",
        ):
            value = _number(getattr(self, name), name)
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
            object.__setattr__(self, name, value)
        if self.match_radius <= 0:
            raise ValueError("match_radius must be positive")
        if self.min_margin > 1:
            raise ValueError("min_margin must be <= 1")


@dataclass(frozen=True)
class MagneticProfile:
    """A manually registered stable magnetic signature, kept only in RAM."""

    profile_id: str
    label: str
    kind: int
    kind_name: str
    center: tuple[float, float, float]
    stddev: tuple[float, float, float]
    ranges: tuple[float, float, float]
    sample_count: int
    window_span_s: float
    registered_at: str | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RecognitionResult:
    """One explainable recognition state emitted after a sample or a tick."""

    status: str
    reason: str
    kind: int | None = None
    kind_name: str | None = None
    window_count: int = 0
    window_span_s: float | None = None
    center: tuple[float, float, float] | None = None
    stddev: tuple[float, float, float] | None = None
    ranges: tuple[float, float, float] | None = None
    profile_id: str | None = None
    label: str | None = None
    similarity: float | None = None
    nearest_distance: float | None = None
    runner_up_distance: float | None = None
    margin: float | None = None
    candidate_id: str | None = None
    confirmation_count: int = 0
    timestamp: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class MagneticRecognizer:
    """Recognize a stable magnetic signature without touching the device."""

    def __init__(self, config: RecognitionConfig | None = None) -> None:
        self.config = config or RecognitionConfig()
        self._window: Deque[MagneticSample] = deque(maxlen=self.config.window_size)
        self._profiles: dict[str, MagneticProfile] = {}
        self._active_kind: int | None = None
        self._last_timestamp: datetime | None = None
        self._candidate_id: str | None = None
        self._confirmation_count = 0
        self._next_profile_number = 1
        self._last_result = RecognitionResult(STATUS_MISSING, "no_sample")

    @property
    def profiles(self) -> tuple[MagneticProfile, ...]:
        return tuple(self._profiles.values())

    @property
    def last_result(self) -> RecognitionResult:
        return self._last_result

    @property
    def window(self) -> tuple[MagneticSample, ...]:
        """A bounded read-only view, useful for offline registration/audit."""

        return tuple(self._window)

    def _reset_window(self) -> None:
        # The timestamp is a stream watermark, not window-local state.  Keep
        # it across resets so delayed samples from a previous segment cannot
        # be accepted after a gap, invalid sample, or sensor change.
        self._window.clear()
        self._active_kind = None
        self._candidate_id = None
        self._confirmation_count = 0

    @staticmethod
    def _canonical_kind_name(kind: int) -> str:
        # KIND_NAMES is a fixed protocol table; never retain a caller's
        # arbitrary kind_name string in the recognition state.
        return KIND_NAMES.get(kind, "unknown")

    @staticmethod
    def _compact_sample(sample: MagneticSample, timestamp: datetime) -> MagneticSample:
        """Copy only bounded protocol fields into the recognition window."""

        return MagneticSample(
            kind=int(sample.kind),
            kind_name=MagneticRecognizer._canonical_kind_name(int(sample.kind)),
            kind_known=True,
            xyz_available=True,
            units="transport_counts",
            x=int(sample.x),
            y=int(sample.y),
            z=int(sample.z),
            payload_hex="",
            frame_hex="",
            source="recognition_window",
            checksum_valid=None,
            host_received_at=timestamp.isoformat(timespec="microseconds"),
            host_time_source="recognizer",
            freshness="unknown",
            age_seconds=None,
            raw_line=None,
        )

    def _result(
        self,
        status: str,
        reason: str,
        *,
        timestamp: datetime | None = None,
        **kwargs: object,
    ) -> RecognitionResult:
        result = RecognitionResult(status=status, reason=reason, timestamp=_iso(timestamp), **kwargs)
        self._last_result = result
        return result

    def reset_stream(self) -> None:
        """Start a separate recorded stream while preserving RAM profiles.

        This is an explicit recording/session boundary, not a recovery action
        for an out-of-order sample.  Within one stream the timestamp watermark
        is retained across every automatic window reset.
        """

        self._reset_window()
        self._last_timestamp = None
        self._last_result = RecognitionResult(STATUS_MISSING, "no_sample")

    def _validate_sample(self, sample: MagneticSample) -> tuple[datetime | None, str | None]:
        if not isinstance(sample, MagneticSample):
            raise TypeError("sample must be a MagneticSample")
        try:
            timestamp = _timestamp(sample.host_received_at, name="sample.host_received_at")
        except ValueError:
            return None, "timestamp_invalid"
        if isinstance(sample.kind, bool) or sample.kind not in SUPPORTED_KINDS or not sample.kind_known or not sample.xyz_available:
            return timestamp, "unsupported_sensor"
        for name in ("x", "y", "z"):
            value = getattr(sample, name)
            try:
                number = _number(value, name)
            except ValueError:
                return timestamp, "invalid_xyz"
            if not isinstance(value, int) or isinstance(value, bool) or not -32768 <= number <= 32767:
                return timestamp, "invalid_xyz"
        return timestamp, None

    def feed(
        self,
        sample: MagneticSample,
        *,
        as_of: str | datetime | None = None,
    ) -> RecognitionResult:
        """Consume one real decoded sample and return its current state."""

        timestamp, invalid_reason = self._validate_sample(sample)
        kind_name = self._canonical_kind_name(sample.kind) if isinstance(sample.kind, int) else "unknown"
        if timestamp is None:
            self._reset_window()
            return self._result(STATUS_MISSING, invalid_reason or "timestamp_missing", kind=sample.kind, kind_name=kind_name)
        if invalid_reason is not None:
            self._reset_window()
            status = STATUS_UNKNOWN if invalid_reason == "unsupported_sensor" else STATUS_MISSING
            return self._result(status, invalid_reason, kind=sample.kind, kind_name=kind_name, timestamp=timestamp)

        reference = _timestamp(as_of, name="as_of") if as_of is not None else timestamp
        assert reference is not None
        age = (reference - timestamp).total_seconds()
        if age < 0 or age > self.config.max_age_s:
            self._reset_window()
            return self._result(
                STATUS_MISSING,
                "future_sample" if age < 0 else "sample_stale",
                kind=sample.kind,
                kind_name=kind_name,
                timestamp=timestamp,
            )

        reset_reason: str | None = None
        sensor_changed = self._active_kind is not None and sample.kind != self._active_kind
        if self._last_timestamp is not None:
            delta = (timestamp - self._last_timestamp).total_seconds()
            if delta <= 0:
                self._reset_window()
                return self._result(STATUS_WARMING_UP, "timestamp_non_monotonic_reset", kind=sample.kind, kind_name=kind_name, timestamp=timestamp)
            if delta > self.config.max_gap_s:
                self._reset_window()
                reset_reason = "timestamp_gap_reset"
        if sensor_changed:
            self._reset_window()
            reset_reason = "sensor_changed_reset"

        self._active_kind = sample.kind
        self._last_timestamp = timestamp
        self._window.append(self._compact_sample(sample, timestamp))
        result = self._evaluate(timestamp, advance_candidate=True)
        if reset_reason is not None:
            return self._result(STATUS_WARMING_UP, reset_reason, kind=sample.kind, kind_name=kind_name, window_count=1, window_span_s=0.0, timestamp=timestamp)
        return result

    def tick(self, as_of: str | datetime) -> RecognitionResult:
        """Evaluate freshness without adding data; never invents a sample."""

        reference = _timestamp(as_of, name="as_of")
        if reference is None or self._last_timestamp is None or not self._window:
            return self._result(STATUS_MISSING, "no_sample", timestamp=reference)
        age = (reference - self._last_timestamp).total_seconds()
        if age < 0:
            last_timestamp = self._last_timestamp
            self._reset_window()
            return self._result(STATUS_MISSING, "future_reference", timestamp=last_timestamp)
        if age > self.config.max_age_s:
            old_kind = self._active_kind
            last_timestamp = self._last_timestamp
            self._reset_window()
            return self._result(STATUS_MISSING, "sample_expired", kind=old_kind, timestamp=last_timestamp)
        return self._evaluate(reference, advance_candidate=False)

    def _window_stats(self) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float], float]:
        values = tuple((float(item.x), float(item.y), float(item.z)) for item in self._window)
        center = tuple(_mean(axis) for axis in zip(*values))
        stddev = tuple(_stddev(axis) for axis in zip(*values))
        ranges = tuple(max(axis) - min(axis) for axis in zip(*values))
        first = _timestamp(self._window[0].host_received_at, name="sample.host_received_at")
        last = _timestamp(self._window[-1].host_received_at, name="sample.host_received_at")
        span = 0.0 if first is None or last is None else max(0.0, (last - first).total_seconds())
        return center, stddev, ranges, span

    def _evaluate(self, timestamp: datetime | None, *, advance_candidate: bool) -> RecognitionResult:
        if not self._window or self._active_kind is None:
            return self._result(STATUS_MISSING, "no_sample", timestamp=timestamp)
        center, stddev, ranges, span = self._window_stats()
        common = dict(
            kind=self._active_kind,
            kind_name=self._canonical_kind_name(self._active_kind),
            window_count=len(self._window),
            window_span_s=span,
            center=center,
            stddev=stddev,
            ranges=ranges,
            timestamp=timestamp,
        )
        if len(self._window) < self.config.window_size:
            self._candidate_id = None
            self._confirmation_count = 0
            return self._result(STATUS_WARMING_UP, "window_not_full", **common)
        if span < self.config.min_window_span_s:
            self._candidate_id = None
            self._confirmation_count = 0
            return self._result(STATUS_WARMING_UP, "window_span_short", **common)
        if any(value > self.config.stable_std_max for value in stddev) or any(value > self.config.stable_range_max for value in ranges):
            self._candidate_id = None
            self._confirmation_count = 0
            return self._result(STATUS_UNSTABLE, "window_variation_high", **common)

        candidates = sorted(
            (
                (_vector_distance(center, profile.center), profile)
                for profile in self._profiles.values()
                if profile.kind == self._active_kind
            ),
            key=lambda item: item[0],
        )
        if not candidates:
            self._candidate_id = None
            self._confirmation_count = 0
            return self._result(STATUS_UNKNOWN, "no_profile_for_sensor", **common)

        nearest_distance, nearest = candidates[0]
        runner_up_distance = candidates[1][0] if len(candidates) > 1 else None
        if runner_up_distance is None:
            margin = 1.0
        else:
            margin = max(0.0, (runner_up_distance - nearest_distance) / max(runner_up_distance, 1e-9))
        similarity = max(0.0, 1.0 - nearest_distance / self.config.match_radius)
        common.update(
            nearest_distance=nearest_distance,
            runner_up_distance=runner_up_distance,
            margin=margin,
            similarity=similarity,
            candidate_id=nearest.profile_id,
        )
        if nearest_distance > self.config.match_radius:
            self._candidate_id = None
            self._confirmation_count = 0
            return self._result(STATUS_UNKNOWN, "nearest_outside_radius", **common)
        if runner_up_distance is not None and margin < self.config.min_margin:
            self._candidate_id = None
            self._confirmation_count = 0
            return self._result(STATUS_AMBIGUOUS, "candidate_margin_small", **common)
        if self._candidate_id == nearest.profile_id and advance_candidate:
            self._confirmation_count = min(self.config.confirmations_required, self._confirmation_count + 1)
        elif self._candidate_id != nearest.profile_id:
            self._candidate_id = nearest.profile_id
            self._confirmation_count = 1 if advance_candidate else 0
        common["confirmation_count"] = self._confirmation_count
        if self._confirmation_count < self.config.confirmations_required:
            return self._result(STATUS_WARMING_UP, "candidate_confirmation_pending", **common)
        common.update(profile_id=nearest.profile_id, label=nearest.label)
        return self._result(STATUS_KNOWN, "candidate_confirmed", **common)

    def register(
        self,
        label: str,
        *,
        as_of: str | datetime,
        profile_id: str | None = None,
    ) -> MagneticProfile:
        """Explicitly archive the current stable window under a human label.

        This method never runs automatically and stores no role/personality.
        It is intentionally legal for a stable window to be ``unknown`` or
        ``ambiguous``: manual registration is how a user may create a new
        shell profile after inspection.
        """

        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 128:
            raise ValueError("label must be a string of 1 through 128 characters")
        now = _timestamp(as_of, name="as_of")
        if now is None:
            raise ValueError("as_of is required for registration")
        result = self.tick(now)
        if result.status in {STATUS_MISSING, STATUS_UNSTABLE} or len(self._window) < self.config.window_size or (result.window_span_s or 0.0) < self.config.min_window_span_s or self._active_kind not in SUPPORTED_KINDS:
            raise ValueError(f"stable window required for registration (status={result.status})")
        if profile_id is None:
            while f"profile-{self._next_profile_number:04d}" in self._profiles:
                self._next_profile_number += 1
            profile_id = f"profile-{self._next_profile_number:04d}"
            self._next_profile_number += 1
        if not isinstance(profile_id, str) or not 1 <= len(profile_id.strip()) <= 64:
            raise ValueError("profile_id must be a string of 1 through 64 characters")
        profile_id = profile_id.strip()
        if profile_id in self._profiles:
            raise ValueError("profile_id already exists; remove it explicitly before re-registering")
        if profile_id not in self._profiles and len(self._profiles) >= self.config.max_profiles:
            raise ValueError("maximum profile count reached")
        center, stddev, ranges, span = self._window_stats()
        sample = self._window[-1]
        profile = MagneticProfile(
            profile_id=profile_id,
            label=label.strip(),
            kind=sample.kind,
            kind_name=sample.kind_name,
            center=center,
            stddev=stddev,
            ranges=ranges,
            sample_count=len(self._window),
            window_span_s=span,
            registered_at=_iso(now),
        )
        self._profiles[profile_id] = profile
        # A new profile changes the candidate set.  Any confirmation made
        # against the old set must not carry across the registration boundary.
        self._candidate_id = None
        self._confirmation_count = 0
        self._evaluate(now, advance_candidate=False)
        return profile

    def unregister(self, profile_id: str) -> bool:
        """Compatibility alias for the explicit profile removal operation."""

        return self.remove_profile(profile_id)

    def remove_profile(self, profile_id: str) -> bool:
        """Explicitly remove one named profile; never infers removal from data."""

        if not isinstance(profile_id, str) or not profile_id.strip():
            raise ValueError("profile_id must be a non-empty string")
        removed = self._profiles.pop(profile_id, None) is not None
        if removed:
            self._candidate_id = None
            self._confirmation_count = 0
            status = STATUS_UNKNOWN if self._window else STATUS_MISSING
            self._result(
                status,
                "profile_removed",
                kind=self._active_kind,
                kind_name=self._canonical_kind_name(self._active_kind) if self._active_kind is not None else None,
                window_count=len(self._window),
                timestamp=self._last_timestamp,
            )
        return removed


__all__ = [
    "MagneticProfile",
    "MagneticRecognizer",
    "RecognitionConfig",
    "RecognitionResult",
    "STATUS_AMBIGUOUS",
    "STATUS_KNOWN",
    "STATUS_MISSING",
    "STATUS_UNKNOWN",
    "STATUS_UNSTABLE",
    "STATUS_WARMING_UP",
]
