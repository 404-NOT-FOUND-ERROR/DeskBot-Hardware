"""Timeline and boundary measurements for the offline circular transition sample."""
from __future__ import annotations

import numpy as np

COMMON_STATES = ('idle', 'happy', 'sad', 'shy', 'confused', 'drowsy')
DEFAULT_TIMELINE = [('idle', 40), ('happy', 40), ('sad', 40), ('idle', 40)]


def validate_timeline(timeline, fps=20) -> list[dict]:
    if type(fps) is not int or not 1 <= fps <= 60 or 60 % fps:
        raise ValueError('fps must divide the original 60 Hz clock')
    if not isinstance(timeline, (list, tuple)) or not 1 <= len(timeline) <= 16:
        raise ValueError('one to sixteen bounded timeline segments required')
    result, start = [], 0
    for item in timeline:
        if (not isinstance(item, (list, tuple)) or len(item) != 2 or
                item[0] not in COMMON_STATES or type(item[1]) is not int or not 1 <= item[1] <= 192):
            raise ValueError('invalid circular state or frame count')
        state, frames = item
        result.append(dict(state=state, start_frame=start, frames=frames))
        start += frames
    if start > 192:
        raise ValueError('timeline exceeds the bounded decoder pixel budget')
    return result


def boundary_metrics(frames: np.ndarray, segments: list[dict]) -> list[dict]:
    """Measure adjacent decoded RGB frames; these numbers are not a perceptual pass threshold."""
    if not isinstance(frames, np.ndarray) or frames.dtype != np.uint8 or frames.ndim != 4 or frames.shape[-1] != 4:
        raise ValueError('decoded uint8 RGBA frame sequence required')
    checked = validate_timeline([(item['state'], item['frames']) for item in segments])
    if checked != segments or sum(item['frames'] for item in segments) != len(frames):
        raise ValueError('timeline and decoded frames differ')
    result = []
    for previous, current in zip(segments, segments[1:]):
        index = current['start_frame']
        # Transparent pixels are composited over the display's black background.
        def visible(i):
            return frames[i, ..., :3].astype(np.uint16) * frames[i, ..., 3:4] // 255
        difference = np.abs(visible(index).astype(np.int16) - visible(index - 1).astype(np.int16))
        window = [round(float(np.abs(visible(i).astype(np.int16) - visible(i - 1).astype(np.int16)).mean()), 5)
                  for i in range(max(1, index - 3), min(len(frames), index + 4))]
        result.append(dict(from_state=previous['state'], to_state=current['state'], frame=index,
                           mean_rgb_delta=round(float(difference.mean()), 5),
                           changed_pixel_fraction=round(float(np.any(difference > 0, axis=-1).mean()), 6),
                           nearby_adjacent_mean_rgb_delta=window))
    return result
