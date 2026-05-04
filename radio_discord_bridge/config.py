"""Environment-variable-based configuration."""
import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw, 0)


def _str(name: str, default: str = "") -> str:
    return os.getenv(name) or default


@dataclass(frozen=True)
class Config:
    # ────── Discord ──────
    discord_token: str = _str("DISCORD_BOT_TOKEN")
    discord_guild_id: int = _int("DISCORD_GUILD_ID", 0)
    discord_voice_channel_id: int = _int("DISCORD_VOICE_CHANNEL_ID", 0)

    # ────── VE-PG4 multicast ──────
    mcast_group: str = _str("BRIDGE_MCAST_GROUP", "239.255.255.1")
    rtp_port: int = _int("BRIDGE_RTP_PORT", 22510)
    rtcp_port: int = _int("BRIDGE_RTCP_PORT", 22511)
    multicast_ttl: int = _int("BRIDGE_MCAST_TTL", 32)
    multicast_iface_ip: str = _str("BRIDGE_MCAST_IFACE_IP", "")

    our_ssrc: int = _int("BRIDGE_SSRC", 0xCAFEBABE)
    rtcp_cname: str = _str("BRIDGE_RTCP_CNAME", "239.255.255.1")
    rtcp_interval_s: float = float(_str("BRIDGE_RTCP_INTERVAL_S", "5"))

    # ────── PTT policy ──────
    ptt_idle_release_ms: int = _int("BRIDGE_PTT_IDLE_RELEASE_MS", 800)

    # ────── Discord → Radio multi-speaker policy ──────
    # "fcfs": only the first speaker is forwarded; others are dropped while held.
    # "mix":  all simultaneous speakers are sum-mixed into one RTP stream.
    discord_mix_mode: str = _str("BRIDGE_DISCORD_MIX_MODE", "fcfs")

    # ────── Discord receive (RX) toggle ──────
    # When upstream voice-recv is broken (e.g. Discord voice gateway v8 issues),
    # set this to "0" / "false" to run TX-only — radio audio still reaches Discord
    # but Discord users can't talk back into the radio.
    discord_rx_enabled: bool = _str("BRIDGE_DISCORD_RX_ENABLED", "1").lower() not in ("0", "false", "no", "off")

    # ────── Audio ──────
    g711_sample_rate: int = 8000
    discord_sample_rate: int = 48000
    frame_ms: int = 20

    @property
    def g711_samples_per_frame(self) -> int:
        return self.g711_sample_rate * self.frame_ms // 1000  # 160

    @property
    def discord_samples_per_frame_stereo(self) -> int:
        return self.discord_sample_rate * self.frame_ms // 1000  # 960 per channel

    @property
    def discord_bytes_per_frame_stereo(self) -> int:
        # 48000 * 0.02 * 2ch * 2byte = 3840
        return self.discord_samples_per_frame_stereo * 2 * 2

    def validate(self) -> None:
        if not self.discord_token:
            raise SystemExit("DISCORD_BOT_TOKEN env required")
        if not self.discord_guild_id:
            raise SystemExit("DISCORD_GUILD_ID env required (numeric server ID)")
        if not self.discord_voice_channel_id:
            raise SystemExit("DISCORD_VOICE_CHANNEL_ID env required (numeric voice channel ID)")
        if self.discord_mix_mode not in ("fcfs", "mix"):
            raise SystemExit(f"BRIDGE_DISCORD_MIX_MODE must be 'fcfs' or 'mix' (got: {self.discord_mix_mode})")
