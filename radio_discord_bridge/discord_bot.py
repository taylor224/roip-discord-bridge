"""
Discord bot — auto-joins a fixed voice channel, exposes a voice-receive sink,
and a live-PCM AudioSource for outbound audio.

Behavior:
  - On startup, joins the configured guild + voice channel.
  - Uses voice-recv extension's AudioSink to receive per-user PCM (48 kHz stereo s16).
  - StreamingPcmSource pushes radio audio to the channel as a live PCM stream.
  - On voice disconnect, retries after 5 s.
"""
import asyncio
import logging
import queue
from typing import Callable, Optional

import discord
from discord.ext import voice_recv

log = logging.getLogger(__name__)

# Discord voice frame size (20 ms @ 48 kHz stereo s16) = 3840 bytes
FRAME_BYTES = 3840
SILENCE_FRAME = b"\x00" * FRAME_BYTES


class StreamingPcmSource(discord.AudioSource):
    """
    Bridges a live PCM queue to Discord's outbound audio frames.
    Discord's internal audio thread calls read() every 20 ms — that call is
    synchronous and must return immediately. push() (called from the asyncio
    loop) feeds frames into the queue.
    """

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
            # Avoid latency build-up — drop the oldest frame.
            try:
                self._q.get_nowait()
                self._q.put_nowait(pcm_3840)
            except queue.Empty:
                pass

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
    """
    Discord → radio direction. Forwards each user's 20 ms PCM frame via callback.
    write() runs on Discord's internal audio thread.
    """

    def __init__(self, on_frame: Callable[[int, bytes], None]):
        super().__init__()
        self._on_frame = on_frame

    def wants_opus(self) -> bool:
        return False  # we want PCM s16 48 kHz stereo

    def write(self, source, data: voice_recv.VoiceData) -> None:
        # source: Member or None (unknown SSRC)
        # data.pcm: 48 kHz stereo s16 (3840 bytes)
        if source is None:
            return
        self._on_frame(source.id, data.pcm)

    def cleanup(self) -> None:
        pass


class DiscordBridgeClient(discord.Client):
    """
    Bot that auto-joins a fixed guild + voice channel.
      on_voice_frame(user_id, pcm_3840): external callback when a user's frame arrives.
      audio_source: external StreamingPcmSource where the bridge pushes radio audio.
    """

    def __init__(
        self,
        guild_id: int,
        voice_channel_id: int,
        on_voice_frame: Callable[[int, bytes], None],
        audio_source: StreamingPcmSource,
    ):
        intents = discord.Intents.default()
        intents.voice_states = True
        intents.guilds = True
        super().__init__(intents=intents)

        self.guild_id = guild_id
        self.voice_channel_id = voice_channel_id
        self._on_voice_frame = on_voice_frame
        self._audio_source = audio_source

        self.voice_client: Optional[voice_recv.VoiceRecvClient] = None
        self._reconnect_task: Optional[asyncio.Task] = None

    async def on_ready(self) -> None:
        log.info("Discord bot ready: %s (id=%s)", self.user, self.user.id if self.user else "?")
        await self._ensure_voice_connection()

    async def on_voice_state_update(self, member, before, after) -> None:
        # If the bot itself was disconnected from voice, reconnect.
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

        # Already connected — skip.
        if self.voice_client and self.voice_client.is_connected():
            return

        try:
            log.info("joining voice channel: guild=%s channel=%s", guild.name, channel.name)
            self.voice_client = await channel.connect(cls=voice_recv.VoiceRecvClient, timeout=20.0)
        except Exception as e:
            log.error("voice connect failed: %s", e)
            await asyncio.sleep(5)
            asyncio.create_task(self._ensure_voice_connection())
            return

        # Start outbound audio — live PCM source.
        if not self.voice_client.is_playing():
            self.voice_client.play(self._audio_source)

        # Start receiving — per-user frame callback.
        sink = RadioSink(self._on_voice_frame)
        self.voice_client.listen(sink)

        log.info("voice ready — playing live source + listening per-user")

    async def shutdown(self) -> None:
        if self.voice_client:
            try:
                self.voice_client.stop_listening()
                self.voice_client.stop()
                await self.voice_client.disconnect(force=True)
            except Exception:
                pass
        await self.close()
