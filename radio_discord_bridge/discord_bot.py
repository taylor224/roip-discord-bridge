"""
Discord bot — auto-joins a fixed voice channel, exposes a voice-receive sink,
and a live-PCM AudioSource for outbound audio.

Implementation: discord.py 2.7 + `discord-ext-voice-recv` for real-time per-user
voice receive.

Important: Discord enforces DAVE end-to-end encryption since March 2026.
Upstream `discord-ext-voice-recv` on PyPI does not yet decrypt DAVE-encrypted
audio (you'll get `OpusError: corrupted stream`). Until upstream PR #54 ships
to PyPI, install voice-recv from the fork at
https://github.com/rdphillips7/discord-ext-voice-recv (already pinned in
pyproject.toml). The fork integrates DAVE decryption into the packet pipeline,
so no additional patching is required here.

Behavior:
  - On startup, joins the configured guild + voice channel.
  - Outbound: StreamingPcmSource pushes radio audio to the channel.
  - Inbound (optional, RX_ENABLED): RadioSink hands per-user 48 kHz stereo PCM
    frames to a callback.
  - On disconnect, retries after 5 s.
"""
import asyncio
import logging
import queue
from typing import Callable, Optional

import discord
from discord.ext import voice_recv

log = logging.getLogger(__name__)

# 20 ms @ 48 kHz stereo s16 = 3840 bytes
FRAME_BYTES = 3840
SILENCE_FRAME = b"\x00" * FRAME_BYTES


class StreamingPcmSource(discord.AudioSource):
    """Bridges a live PCM queue to Discord's outbound audio frames (20 ms cadence)."""

    def __init__(self, max_queue: int = 50):
        self._q: "queue.Queue[bytes]" = queue.Queue(maxsize=max_queue)
        self._closed = False

    def push(self, pcm_3840: bytes) -> None:
        if self._closed:
            return
        if len(pcm_3840) != FRAME_BYTES:
            log.warning("push: bad frame size %d (expected %d)", len(pcm_3840), FRAME_BYTES)
            return
        try:
            self._q.put_nowait(pcm_3840)
        except queue.Full:
            try:
                self._q.get_nowait()
                self._q.put_nowait(pcm_3840)
            except queue.Empty:
                pass

    def clear(self) -> None:
        """Drain pending frames — used when transitioning radio→idle to avoid stale audio."""
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                return

    def read(self) -> bytes:
        if self._closed:
            return b""
        try:
            return self._q.get_nowait()
        except queue.Empty:
            return SILENCE_FRAME

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        self._closed = True


class RadioSink(voice_recv.AudioSink):
    """Per-user 20 ms PCM frame → callback (called on Discord's internal thread)."""

    def __init__(self, on_frame: Callable[[int, bytes], None]):
        super().__init__()
        self._on_frame = on_frame
        self._null_source_logged = False
        self._packet_count = 0

    def wants_opus(self) -> bool:
        return False

    def write(self, source, data: voice_recv.VoiceData) -> None:
        self._packet_count += 1
        if self._packet_count in (1, 10, 100):
            log.info("RadioSink.write: packet #%d source=%s pcm_len=%d",
                     self._packet_count,
                     getattr(source, "id", None),
                     len(getattr(data, "pcm", b"") or b""))

        if source is None:
            if not self._null_source_logged:
                log.warning(
                    "RadioSink: received audio packet with source=None — "
                    "Discord SSRC→user mapping not yet established (SPEAKING event missing). "
                    "Audio will be dropped until the mapping arrives."
                )
                self._null_source_logged = True
            return

        pcm = getattr(data, "pcm", None)
        if not pcm:
            return
        self._on_frame(source.id, pcm)

    def cleanup(self) -> None:
        pass


class DiscordBridgeClient(discord.Client):
    """Bot that auto-joins a fixed guild + voice channel."""

    def __init__(
        self,
        guild_id: int,
        voice_channel_id: int,
        on_voice_frame: Callable[[int, bytes], None],
        audio_source: StreamingPcmSource,
        rx_enabled: bool = True,
    ):
        intents = discord.Intents.default()
        intents.voice_states = True
        intents.guilds = True
        super().__init__(intents=intents)

        self.guild_id = guild_id
        self.voice_channel_id = voice_channel_id
        self._on_voice_frame = on_voice_frame
        self._audio_source = audio_source
        self._rx_enabled = rx_enabled

        self.voice_client: Optional[voice_recv.VoiceRecvClient] = None

    async def on_ready(self) -> None:
        log.info("Discord bot ready: %s (id=%s)", self.user, self.user.id if self.user else "?")
        await self._ensure_voice_connection()

    async def on_voice_state_update(self, member, before, after) -> None:
        if self.user is None or member.id != self.user.id:
            return
        if before.channel and not after.channel:
            log.warning("bot was disconnected from voice; will reconnect")
            await asyncio.sleep(5)
            await self._ensure_voice_connection()

    async def _ensure_voice_connection(self) -> None:
        guild = self.get_guild(self.guild_id)
        if guild is None:
            log.error("guild not found: %s", self.guild_id)
            return
        channel = guild.get_channel(self.voice_channel_id)
        if not isinstance(channel, discord.VoiceChannel):
            log.error("voice channel not found or not a VoiceChannel: %s", self.voice_channel_id)
            return

        if self.voice_client and self.voice_client.is_connected():
            return

        try:
            log.info("joining voice channel: guild=%s channel=%s", guild.name, channel.name)
            cls = voice_recv.VoiceRecvClient if self._rx_enabled else discord.VoiceClient
            self.voice_client = await channel.connect(cls=cls, timeout=20.0)
        except Exception as e:
            log.error("voice connect failed: %s", e)
            await asyncio.sleep(5)
            asyncio.create_task(self._ensure_voice_connection())
            return

        # Outbound — live PCM source. Start paused so the bot doesn't appear
        # as continuously speaking; bridge resumes it when radio audio arrives.
        if not self.voice_client.is_playing():
            self.voice_client.play(self._audio_source)
            self.voice_client.pause()

        # Inbound — only if enabled.
        if self._rx_enabled and isinstance(self.voice_client, voice_recv.VoiceRecvClient):
            sink = RadioSink(self._on_voice_frame)
            self.voice_client.listen(sink)
            log.info("voice ready — playing live source + listening per-user (RX enabled)")
        else:
            log.info("voice ready — playing live source (RX disabled — TX-only mode)")

    async def shutdown(self) -> None:
        if self.voice_client:
            try:
                if isinstance(self.voice_client, voice_recv.VoiceRecvClient):
                    self.voice_client.stop_listening()
                self.voice_client.stop()
                await self.voice_client.disconnect(force=True)
            except Exception:
                pass
        await self.close()
