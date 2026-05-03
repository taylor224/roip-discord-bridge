"""
Per-user PCM frame buffer + mixer for Discord → radio direction.

Discord's voice-recv extension fires the sink's write() per user, per ~20 ms,
only while that user is actually speaking. The Bridge pushes each frame here.
Every 20 ms the orchestrator calls tick() which:
  - returns None if no user produced audio in this window, or
  - returns the latest frame if exactly one user was active, or
  - returns a sum-mixed (clip-protected) frame if multiple users were active.

We keep ONLY the most recent frame per user between ticks. This keeps end-to-end
latency bounded under network jitter — older frames are dropped rather than
queued.
"""
import threading
from typing import Optional

import numpy as np

from .discord_bot import FRAME_BYTES


def mix_s16_pcm(frames: list[bytes]) -> bytes:
    """Sum mix of N frames, then clip to int16 range. All frames must be same length."""
    if len(frames) == 1:
        return frames[0]
    samples = len(frames[0]) // 2  # bytes → int16 count
    accum = np.zeros(samples, dtype=np.int32)
    for f in frames:
        accum += np.frombuffer(f, dtype=np.int16).astype(np.int32)
    return np.clip(accum, -32768, 32767).astype(np.int16).tobytes()


class Mixer:
    """Thread-safe per-user latest-frame buffer with sum-mix tick."""

    def __init__(self) -> None:
        self._latest: dict[int, bytes] = {}
        self._lock = threading.Lock()

    def push(self, user_id: int, pcm_3840: bytes) -> None:
        if len(pcm_3840) != FRAME_BYTES:
            return
        with self._lock:
            # Overwrite — only keep the newest frame per user.
            self._latest[user_id] = pcm_3840

    def tick(self) -> tuple[Optional[bytes], int]:
        """
        Drain all buffered frames and return (mixed_frame_or_None, active_user_count).
        active_user_count > 0 indicates Discord was active this window.
        """
        with self._lock:
            if not self._latest:
                return None, 0
            frames = list(self._latest.values())
            self._latest.clear()
        return mix_s16_pcm(frames), len(frames)

    def clear(self) -> None:
        with self._lock:
            self._latest.clear()
