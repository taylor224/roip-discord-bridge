# radio_to_discord

Bidirectional voice bridge between **VE-PG4** (ICOM RoIP Gateway) and a **Discord** voice channel.

## Overview

```
[Physical radio] ─PTT/audio─ [VE-PG4]
                              │ RTP G.711μ (8 kHz mono)
                              │ Multicast 239.255.255.1:22510
                              ▼
                      [radio_discord_bridge]   ─Opus 48k stereo─→ [Discord voice channel]
                              ▲                                          │
                              │                                          ▼
                      [radio_discord_bridge]   ←Opus 48k stereo─ [Discord users' mics]
                              │ RTP G.711μ
                              ▼
                         [VE-PG4] ─PTT keying─ [Physical radio]
```

- **Radio → Discord**: receive multicast RTP G.711μ from VE-PG4, decode to PCM, upsample 8 kHz mono → 48 kHz stereo, push to the Discord voice channel.
- **Discord → Radio**: per-user 48 kHz stereo PCM → mono → 8 kHz → G.711μ → multicast RTP → VE-PG4 keys the radio's PTT.
- **PTT policy**: First-come-first-serve. While one side is actively speaking, the other side is gated. After 800 ms of silence the gate auto-releases.
- For multiple Discord speakers at once, only the first speaker is forwarded to the radio (the radio is half-duplex, so mixing makes the audio unintelligible).
- RTCP keepalive is emitted every 5 s using the same RR+SDES format as VE-PG4.

## Prerequisites — Discord bot setup

1. **Discord Developer Portal** (https://discord.com/developers/applications): create a new Application.
2. Open the **Bot** tab → "Reset Token" → copy the token (use as `DISCORD_BOT_TOKEN`).
3. Under **Privileged Gateway Intents**, enable:
   - `SERVER MEMBERS INTENT` (optional)
   - `MESSAGE CONTENT INTENT` (optional)
4. **OAuth2 → URL Generator**:
   - Scopes: `bot`
   - Bot Permissions: `Connect`, `Speak`, `Use Voice Activity`
   - Open the generated URL to invite the bot to your server.
5. In the **Discord client**:
   - User settings → Advanced → enable Developer Mode
   - Right-click your server → "Copy Server ID" → set as `DISCORD_GUILD_ID`
   - Right-click the target voice channel → "Copy Channel ID" → set as `DISCORD_VOICE_CHANNEL_ID`

## Install & run

### Local (Linux / macOS)

```bash
cd radio_to_discord

# System libraries (macOS)
brew install opus libsodium

# Python dependencies
pip install -e .

# Configure
cp .env.example .env
# Edit .env — fill in DISCORD_BOT_TOKEN, GUILD_ID, VOICE_CHANNEL_ID
export $(cat .env | xargs)

# Run
python -m radio_discord_bridge
```

### Windows

`discord.py[voice]` ships a bundled `libopus` DLL, so no extra install is required.

```powershell
# Python dependencies
pip install -e .

# Allow inbound UDP through Windows Defender Firewall (Administrator, once)
New-NetFirewallRule -DisplayName "RadioBridge UDP 22510" `
    -Direction Inbound -Protocol UDP -LocalPort 22510 -Action Allow
New-NetFirewallRule -DisplayName "RadioBridge UDP 22511" `
    -Direction Inbound -Protocol UDP -LocalPort 22511 -Action Allow

# Load .env into the current process
Get-Content .env | ForEach-Object {
    if ($_ -match '^([^#=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), 'Process')
    }
}

# If the host has multiple NICs (or a VPN), pin the outgoing IP that's on the
# same LAN as the VE-PG4
$env:BRIDGE_MCAST_IFACE_IP = "192.168.X.Y"

# Run
python -m radio_discord_bridge
```

In `cmd.exe`, use `set DISCORD_BOT_TOKEN=...` instead of PowerShell syntax.

### Docker

```bash
docker build -t radio-to-discord .

docker run --rm \
  --network host \
  -e DISCORD_BOT_TOKEN=... \
  -e DISCORD_GUILD_ID=... \
  -e DISCORD_VOICE_CHANNEL_ID=... \
  radio-to-discord
```

`--network host` is required — multicast IGMP joins generally do not work inside the default container network.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `DISCORD_BOT_TOKEN` | (required) | Discord bot token |
| `DISCORD_GUILD_ID` | (required) | Numeric server ID |
| `DISCORD_VOICE_CHANNEL_ID` | (required) | Numeric voice channel ID |
| `BRIDGE_MCAST_GROUP` | `239.255.255.1` | VE-PG4 RoIP multicast group |
| `BRIDGE_RTP_PORT` | `22510` | RTP port |
| `BRIDGE_RTCP_PORT` | `22511` | RTCP port |
| `BRIDGE_MCAST_TTL` | `32` | Multicast TTL |
| `BRIDGE_MCAST_IFACE_IP` | (auto) | Outgoing interface IP (when host has multiple NICs) |
| `BRIDGE_SSRC` | `0xCAFEBABE` | Our RTP/RTCP SSRC |
| `BRIDGE_RTCP_CNAME` | `239.255.255.1` | RTCP SDES CNAME |
| `BRIDGE_RTCP_INTERVAL_S` | `5` | RTCP send interval (seconds) |
| `BRIDGE_PTT_IDLE_RELEASE_MS` | `800` | PTT auto-release silence threshold (ms) |
| `BRIDGE_DISCORD_MIX_MODE` | `fcfs` | Multi-speaker policy. `fcfs` = only the first speaker is forwarded; `mix` = all simultaneous speakers are sum-mixed into one stream. |

## Architecture

`radio_discord_bridge/` modules:

| File | Role |
|---|---|
| `__main__.py` | CLI entry point |
| `config.py` | Env-based configuration |
| `codec.py` | G.711μ + 8k↔48k resample + mono↔stereo |
| `rtp.py` | RTP / RTCP build/parse (VE-PG4 compatible) |
| `multicast.py` | Multicast socket helper (IGMP join) |
| `ptt.py` | FCFS PTT state machine |
| `discord_bot.py` | Discord bot client + voice receive sink + live PCM AudioSource |
| `mixer.py` | Per-user PCM frame buffer + sum-mix (used when `BRIDGE_DISCORD_MIX_MODE=mix`) |
| `bridge.py` | Orchestrator |

## Dependencies

- **discord.py 2.4+** with `voice` extras (PyNaCl + opuslib)
- **discord-ext-voice-recv** — real-time per-user voice receive
- **numpy / scipy** — resampling
- System: `libopus`, `libsodium`

## VE-PG4 prerequisite

- The RoIP Gateway port must be a member of the SelCall group used by your radios on the VE-PG4 (under `Destination Settings`). Without this, RX/TX between the radio and the RoIP layer is not routed.
- See `PROTOCOL.md` for full wire-format details.

## Multi-speaker mix mode

When you set `BRIDGE_DISCORD_MIX_MODE=mix`, the bridge collects all
simultaneously-active Discord speakers into a 20 ms scheduling window, sum-mixes
their PCM frames (with int16 clipping protection), and emits a single RTP
stream to the radio. Only the latest frame per user per window is kept, so
network jitter does not accumulate latency.

```
[user A 48k stereo]──┐
[user B 48k stereo]──┼─→ Mixer.tick() ──→ sum + clip ──→ 48k stereo ──→ ulaw 8k mono ──→ RTP
[user C 48k stereo]──┘    (every 20 ms)
```

Trade-offs:
- Simple sum mixing can clip when many people scream at once. For typical 2–4
  concurrent speakers it's fine.
- The radio remains half-duplex — when the radio side holds the gate
  (`Holder.RADIO`), the mixed stream is still suppressed.

## Roadmap / known limits

- Per-Discord-user volume normalization (AGC)
- Smarter mix (e.g. ducking when many speakers, automatic loudness limiter)
