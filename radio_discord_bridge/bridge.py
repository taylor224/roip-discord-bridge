"""
Discord ↔ VE-PG4 bridge orchestrator.

Tasks:
  - radio_to_discord: receive multicast RTP → G.711μ decode → 8k mono → 48k stereo → push to Discord queue
  - discord_to_radio: per-frame in FCFS mode, or every-20ms ticked sum-mix in MIX mode
  - rtcp_keepalive: emit RTCP RR+SDES every 5 s (VE-PG4 compatible)

PTT FCFS — while one side holds, the other is gated. Auto-released after
`ptt_idle_release_ms` of silence.

Multi-speaker policy (Discord → Radio):
  fcfs (default): only the first speaker is forwarded; others are dropped.
  mix:            all simultaneous speakers are sum-mixed into one RTP stream.
"""
import asyncio
import logging
import random
import threading
from typing import Optional

from .codec import (
    discord_48k_stereo_to_ulaw_8k_mono,
    ulaw_8k_mono_to_discord_48k_stereo,
)
from .config import Config
from .discord_bot import DiscordBridgeClient, StreamingPcmSource, FRAME_BYTES
from .mixer import Mixer
from .multicast import open_multicast_socket
from .ptt import Holder, PttState
from .rtp import build_rtcp_rr_sdes, build_rtp, parse_rtp

log = logging.getLogger(__name__)


class Bridge:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.ptt = PttState(cfg.ptt_idle_release_ms)
        self._ptt_lock = threading.Lock()  # guards FCFS holder identification

        self.rtp_sock = open_multicast_socket(
            cfg.mcast_group, cfg.rtp_port,
            cfg.multicast_iface_ip or None, cfg.multicast_ttl
        )
        self.rtcp_sock = open_multicast_socket(
            cfg.mcast_group, cfg.rtcp_port,
            cfg.multicast_iface_ip or None, cfg.multicast_ttl
        )

        # Live PCM source for radio → Discord direction.
        self.audio_source = StreamingPcmSource(max_queue=50)

        # FCFS-held Discord speaker (FCFS mode only — None in MIX mode).
        self._discord_speaker_id: Optional[int] = None

        # Per-user buffer + sum-mixer (MIX mode only).
        self._mixer = Mixer() if cfg.discord_mix_mode == "mix" else None

        # TX (Discord → radio) RTP state.
        self._tx_seq = random.randint(0, 0xFFFF)
        self._tx_ts = random.randint(0, 0xFFFFFFFF)

        # Reference to the main asyncio loop (used from Discord sink thread).
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Discord client.
        self.discord_client = DiscordBridgeClient(
            guild_id=cfg.discord_guild_id,
            voice_channel_id=cfg.discord_voice_channel_id,
            on_voice_frame=self._on_discord_voice_frame,
            audio_source=self.audio_source,
            rx_enabled=cfg.discord_rx_enabled,
        )

        log.info(
            "multi-speaker policy: %s  RX: %s",
            cfg.discord_mix_mode,
            "enabled" if cfg.discord_rx_enabled else "disabled (TX-only)",
        )

    # ── Discord → Radio (TX) ─────────────────────────────────────────────
    def _on_discord_voice_frame(self, user_id: int, pcm_stereo_48k: bytes) -> None:
        """
        20 ms frame from a Discord user (called on Discord's internal thread).
        Routes to FCFS path or MIX path based on cfg.discord_mix_mode.
        """
        # Log first frame per user (rate-limit) for debugging.
        if not hasattr(self, "_seen_users"):
            self._seen_users = set()
        if user_id not in self._seen_users:
            self._seen_users.add(user_id)
            log.info("discord voice frame received from user_id=%d (len=%d)", user_id, len(pcm_stereo_48k))

        if len(pcm_stereo_48k) != FRAME_BYTES:
            return

        if self._mixer is not None:
            # MIX mode — just buffer; the _mix_send_loop ticks every 20 ms.
            self._mixer.push(user_id, pcm_stereo_48k)
            return

        # ── FCFS mode ──
        with self._ptt_lock:
            holder = self.ptt.holder
            if holder is Holder.RADIO:
                return  # radio holds the channel
            # Another Discord user already holds — drop this user.
            if (holder is Holder.DISCORD
                    and self._discord_speaker_id is not None
                    and self._discord_speaker_id != user_id):
                return

            if not self.ptt.acquire(Holder.DISCORD):
                return
            self._discord_speaker_id = user_id
            self.ptt.touch(Holder.DISCORD)

            self._send_to_radio(pcm_stereo_48k)

    def _send_to_radio(self, pcm_stereo_48k: bytes) -> None:
        """Convert one 48k stereo frame to G.711μ and send as RTP. Caller holds _ptt_lock."""
        try:
            ulaw = discord_48k_stereo_to_ulaw_8k_mono(pcm_stereo_48k)
        except Exception as e:
            log.warning("discord→radio conversion failed: %s", e)
            return
        pkt = build_rtp(
            seq=self._tx_seq,
            ts=self._tx_ts,
            payload=ulaw,
            ssrc=self.cfg.our_ssrc,
            marker=False,
            pt=0,
        )
        try:
            self.rtp_sock.sendto(pkt, (self.cfg.mcast_group, self.cfg.rtp_port))
        except OSError as e:
            log.warning("rtp send failed: %s", e)

        # Debug: log first packet + every 50th to confirm we're actually sending.
        self._tx_packet_count = getattr(self, "_tx_packet_count", 0) + 1
        if self._tx_packet_count in (1, 50, 100):
            log.info(
                "→ radio RTP #%d sent: seq=%d ssrc=0x%08x dst=%s:%d ulaw_len=%d",
                self._tx_packet_count, self._tx_seq, self.cfg.our_ssrc,
                self.cfg.mcast_group, self.cfg.rtp_port, len(ulaw),
            )

        self._tx_seq = (self._tx_seq + 1) & 0xFFFF
        self._tx_ts = (self._tx_ts + self.cfg.g711_samples_per_frame) & 0xFFFFFFFF

    async def _mix_send_loop(self) -> None:
        """
        MIX mode only. Every 20 ms, drain per-user buffers, sum-mix, and emit one
        RTP packet to the radio. Maintains 20 ms cadence with monotonic scheduling.
        """
        if self._mixer is None:
            return
        loop = asyncio.get_running_loop()
        next_t = loop.time()
        period = self.cfg.frame_ms / 1000  # 0.020

        while True:
            next_t += period
            sleep_for = next_t - loop.time()
            if sleep_for > 0:
                try:
                    await asyncio.sleep(sleep_for)
                except asyncio.CancelledError:
                    return
            else:
                # Fell behind — skip the catch-up to avoid burst sending.
                next_t = loop.time()

            mixed, n_users = self._mixer.tick()
            if mixed is None:
                continue

            with self._ptt_lock:
                holder = self.ptt.holder
                if holder is Holder.RADIO:
                    if not getattr(self, "_radio_block_logged", False):
                        log.info("mix_send_loop: blocked by RADIO holder — dropping discord audio")
                        self._radio_block_logged = True
                    continue
                self._radio_block_logged = False
                if not self.ptt.acquire(Holder.DISCORD):
                    continue
                self.ptt.touch(Holder.DISCORD)
                self._send_to_radio(mixed)

    # ── Radio → Discord (RX) ─────────────────────────────────────────────
    async def _radio_to_discord_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                data = await loop.sock_recv(self.rtp_sock, 2048)
            except (asyncio.CancelledError, OSError):
                return
            rtp = parse_rtp(data)
            if rtp is None or rtp.pt != 0:
                continue
            if rtp.ssrc == self.cfg.our_ssrc:
                continue  # drop our own echo

            # FCFS — if Discord holds, suppress radio (radio is half-duplex anyway).
            with self._ptt_lock:
                holder = self.ptt.holder
                if holder is Holder.DISCORD:
                    continue
                if not self.ptt.acquire(Holder.RADIO):
                    continue
                self.ptt.touch(Holder.RADIO)

            try:
                stereo = ulaw_8k_mono_to_discord_48k_stereo(rtp.payload)
            except Exception as e:
                log.warning("radio→discord conversion failed: %s", e)
                continue

            self.audio_source.push(stereo)

            # Resume Discord playback now that radio is actively speaking.
            vc = self.discord_client.voice_client
            if vc is not None and vc.is_connected() and vc.is_paused():
                try:
                    vc.resume()
                except Exception as e:
                    log.warning("voice resume failed: %s", e)

    # ── RTCP keepalive ─────────────────────────────────────────────────
    async def _rtcp_keepalive_loop(self) -> None:
        cname = self.cfg.rtcp_cname.encode()
        pkt = build_rtcp_rr_sdes(self.cfg.our_ssrc, cname)
        while True:
            try:
                self.rtcp_sock.sendto(pkt, (self.cfg.mcast_group, self.cfg.rtcp_port))
            except OSError as e:
                log.warning("rtcp send failed: %s", e)
            await asyncio.sleep(self.cfg.rtcp_interval_s)

    # ── PTT idle watchdog ─────────────────────────────────────────────
    async def _ptt_watchdog_loop(self) -> None:
        """
        When the PTT gate releases (idle), clear per-mode side state. When the
        radio side specifically releases (RADIO → IDLE), also pause Discord
        playback and drain pending PCM so the bot stops appearing as speaking.
        """
        check_s = self.cfg.ptt_idle_release_ms / 1000 / 2
        prev_holder = Holder.IDLE
        while True:
            await asyncio.sleep(check_s)
            with self._ptt_lock:
                holder = self.ptt.holder
                if holder is Holder.IDLE:
                    self._discord_speaker_id = None
                    if self._mixer is not None:
                        self._mixer.clear()

            # RADIO → IDLE transition: pause TX to Discord and drop stale frames.
            if prev_holder is Holder.RADIO and holder is Holder.IDLE:
                self.audio_source.clear()
                vc = self.discord_client.voice_client
                if vc is not None and vc.is_connected() and vc.is_playing():
                    try:
                        vc.pause()
                    except Exception as e:
                        log.warning("voice pause failed: %s", e)
            prev_holder = holder

    # ── Main ──────────────────────────────────────────────────────────
    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()

        discord_task = asyncio.create_task(
            self.discord_client.start(self.cfg.discord_token)
        )

        tasks = [
            self._radio_to_discord_loop(),
            self._rtcp_keepalive_loop(),
            self._ptt_watchdog_loop(),
            discord_task,
        ]
        if self._mixer is not None:
            tasks.append(self._mix_send_loop())

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass

    async def shutdown(self) -> None:
        await self.discord_client.shutdown()
        try:
            self.rtp_sock.close()
            self.rtcp_sock.close()
        except OSError:
            pass
