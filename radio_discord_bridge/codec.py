"""G.711 μ-law (8 kHz mono) ↔ Discord PCM (48 kHz stereo s16) conversions."""
import numpy as np
from scipy import signal


# ── G.711 μ-law (ITU-T G.711) ─────────────────────────────────────────────
def _linear_to_ulaw(s: int) -> int:
    BIAS, CLIP = 0x84, 32635
    sign = 0x80 if s < 0 else 0
    if s < 0:
        s = -s
    if s > CLIP:
        s = CLIP
    s += BIAS
    val = (s >> 7) & 0xFF
    seg = 0
    while val > 1 and seg < 7:
        val >>= 1
        seg += 1
    return (~(sign | (seg << 4) | ((s >> (seg + 3)) & 0x0F))) & 0xFF


def _ulaw_to_linear(u: int) -> int:
    u = ~u & 0xFF
    sign = u & 0x80
    seg = (u >> 4) & 0x07
    mant = u & 0x0F
    s = ((mant << 3) + 0x84) << seg
    s -= 0x84
    return -s if sign else s


_ENC = np.array(
    [_linear_to_ulaw(s if s < 32768 else s - 65536) for s in range(65536)],
    dtype=np.uint8,
)
_DEC = np.array([_ulaw_to_linear(u) for u in range(256)], dtype=np.int16)


def pcm_s16_to_ulaw(pcm: bytes) -> bytes:
    arr = np.frombuffer(pcm, dtype=np.int16).astype(np.uint16)
    return _ENC[arr].tobytes()


def ulaw_to_pcm_s16(ulaw: bytes) -> bytes:
    arr = np.frombuffer(ulaw, dtype=np.uint8)
    return _DEC[arr].astype(np.int16).tobytes()


# ── 8 kHz ↔ 48 kHz resample (mono) ───────────────────────────────────────
def upsample_8k_to_48k_mono(pcm_s16_8k_mono: bytes) -> bytes:
    arr = np.frombuffer(pcm_s16_8k_mono, dtype=np.int16).astype(np.float32)
    out = signal.resample_poly(arr, up=6, down=1)
    return np.clip(out, -32768, 32767).astype(np.int16).tobytes()


def downsample_48k_to_8k_mono(pcm_s16_48k_mono: bytes) -> bytes:
    arr = np.frombuffer(pcm_s16_48k_mono, dtype=np.int16).astype(np.float32)
    out = signal.resample_poly(arr, up=1, down=6)
    return np.clip(out, -32768, 32767).astype(np.int16).tobytes()


# ── mono ↔ stereo (interleaved L/R) ──────────────────────────────────────
def mono_to_stereo(pcm_s16_mono: bytes) -> bytes:
    """[L0, L1, ...] → [L0, L0, L1, L1, ...] — duplicate the mono signal to L+R."""
    arr = np.frombuffer(pcm_s16_mono, dtype=np.int16)
    interleaved = np.empty(arr.size * 2, dtype=np.int16)
    interleaved[0::2] = arr
    interleaved[1::2] = arr
    return interleaved.tobytes()


def stereo_to_mono(pcm_s16_stereo: bytes) -> bytes:
    """[L0, R0, L1, R1, ...] → [(L0+R0)/2, ...] — average L/R into mono."""
    arr = np.frombuffer(pcm_s16_stereo, dtype=np.int16).astype(np.int32)
    left = arr[0::2]
    right = arr[1::2]
    mono = (left + right) // 2
    return np.clip(mono, -32768, 32767).astype(np.int16).tobytes()


# ── End-to-end helpers ───────────────────────────────────────────────────
def ulaw_8k_mono_to_discord_48k_stereo(ulaw_payload: bytes) -> bytes:
    """VE-PG4 RTP payload (160 B μ-law) → one Discord frame (3840 B, 48k stereo s16)."""
    pcm_8k = ulaw_to_pcm_s16(ulaw_payload)        # 320 B  (160 samples s16)
    pcm_48k = upsample_8k_to_48k_mono(pcm_8k)     # 1920 B (960 samples s16 mono)
    return mono_to_stereo(pcm_48k)                 # 3840 B (960 stereo)


def discord_48k_stereo_to_ulaw_8k_mono(pcm_stereo: bytes) -> bytes:
    """One Discord frame (3840 B) → VE-PG4 RTP payload (160 B μ-law)."""
    pcm_mono_48k = stereo_to_mono(pcm_stereo)      # 1920 B
    pcm_mono_8k = downsample_48k_to_8k_mono(pcm_mono_48k)  # 320 B
    return pcm_s16_to_ulaw(pcm_mono_8k)            # 160 B
