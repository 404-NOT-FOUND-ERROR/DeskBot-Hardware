"""Seam and storage checks for the composable, offline circular role package."""
from __future__ import annotations

import hashlib
from collections.abc import Mapping

STATES = ('idle', 'happy', 'sad', 'shy', 'confused', 'drowsy')
LABELS = dict(zip(STATES, ('待机', '开心', '难过', '害羞', '困惑', '困倦')))
PHASES = dict(entry=11, loop=49, exit=11)
FPS = 20
BASE_PAYLOAD_BYTES = 5_981_260
PARTITION_BYTES = 8 * 1024 * 1024
SOFT_BUDGET_BYTES = 7 * 1024 * 1024


def frame_digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def verify_seams(endpoints: Mapping) -> dict:
    """Check decoded RGBA, not file hashes: differently encoded frames may match."""
    if set(endpoints) != set(STATES):
        raise ValueError('all six expression states are required')
    home = endpoints['idle']['entry'][0]
    expected_bytes = 360 * 360 * 4
    for state in STATES:
        if set(endpoints[state]) != set(PHASES):
            raise ValueError(f'three phases required for {state}')
        for pair in endpoints[state].values():
            if (not isinstance(pair, (tuple, list)) or len(pair) != 2 or
                    any(not isinstance(frame, bytes) or len(frame) != expected_bytes for frame in pair)):
                raise ValueError('two full decoded RGBA endpoint frames required')
        entry, loop, exit_ = [endpoints[state][phase] for phase in PHASES]
        if entry[0] != home or exit_[1] != home:
            raise ValueError(f'neutral seam mismatch: {state}')
        if not entry[1] == loop[0] == loop[1] == exit_[0]:
            raise ValueError(f'pose or loop seam mismatch: {state}')
    pairs = []
    for source in STATES:
        for target in STATES:
            if source == target:
                continue
            if endpoints[source]['exit'][1] != endpoints[target]['entry'][0]:
                raise ValueError(f'cross-expression seam mismatch: {source} -> {target}')
            pairs.append(dict(source=source, target=target, pixels_match=True))
    return dict(result='passed', neutral_rgba_sha256=frame_digest(home),
                pose_rgba_sha256={state: frame_digest(endpoints[state]['loop'][0]) for state in STATES},
                phase_seams=6 * 4, directed_pairs=pairs, directed_pair_count=len(pairs))


def capacity(files: Mapping[str, bytes], base_bytes: int = BASE_PAYLOAD_BYTES) -> dict:
    """Count identical complete EAFs once; packaging overhead is measured later."""
    if type(base_bytes) is not int or base_bytes < 0 or not files:
        raise ValueError('nonnegative base payload and nonempty files required')
    unique = {}
    for name, raw in files.items():
        if not isinstance(name, str) or not isinstance(raw, bytes) or not raw:
            raise ValueError('named nonempty binary files required')
        unique.setdefault(frame_digest(raw), len(raw))
    unique_bytes = sum(unique.values())
    combined = base_bytes + unique_bytes
    return dict(logical_files=len(files), unique_files=len(unique),
                logical_bytes=sum(map(len, files.values())), unique_eaf_bytes=unique_bytes,
                base_payload_bytes=base_bytes, combined_payload_estimate_bytes=combined,
                soft_budget_bytes=SOFT_BUDGET_BYTES, partition_bytes=PARTITION_BYTES,
                soft_remaining_estimate_bytes=SOFT_BUDGET_BYTES - combined,
                physical_remaining_estimate_bytes=PARTITION_BYTES - combined,
                estimate_within_soft_budget=combined <= SOFT_BUDGET_BYTES,
                estimate_within_partition=combined <= PARTITION_BYTES,
                package_overhead_included=False)
