# VE-PG4 RoIP Gateway protocol (summary)

Wire-format conventions this bridge depends on. Determined by firmware reverse engineering plus packet capture analysis.

## Mode

The VE-PG4 RoIP Gateway port operates over **standard RTP/RTCP** — separate from ICOM's proprietary BRG codec used in Transceiver mode. This bridge only uses the RTP mode.

## Transport

| Item | Value |
|---|---|
| Transmission Mode | Multicast |
| Multicast group (UI default) | `239.255.255.1` |
| RTP port | `22510` |
| RTCP port | `22511` (RTP+1) |
| Multicast TTL (UI default) | `1` |

## RTP header (RFC 3550 standard)

```
0                   1                   2                   3
0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|V=2|P|X|  CC   |M|     PT      |       sequence number         |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                           timestamp                           |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                           SSRC                                |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                          payload (G.711μ)                     |
+...
```

| Field | Value |
|---|---|
| V (version) | 2 |
| P (padding) | 0 |
| X (extension) | 0 — not used (12-byte standard header only) |
| CC (CSRC count) | 0 |
| M (marker) | **VE-PG4 does not set the marker bit** (always 0). PTT boundaries are inferred from stream activity. |
| PT (payload type) | **0 (PCMU / G.711 μ-law)** |
| sequence | 16-bit big-endian, +1 per packet |
| timestamp | 32-bit big-endian, +160 per packet (= 20 ms @ 8 kHz) |
| SSRC | 32-bit, sender-unique. VE-PG4 regenerates on restart. |
| payload | 160 bytes G.711 μ-law (= 20 ms @ 8 kHz mono) |

## Codec

| Item | Value |
|---|---|
| Codec | G.711 μ-law (PCMU, RFC 3551 PT=0) |
| Sample rate | 8 kHz mono |
| Frame | 20 ms = 160 samples = 160 bytes |

## RTCP keepalive

VE-PG4 emits this every 5 seconds. The bridge mirrors the same shape.

```
80 c9 00 01 <SSRC>                       # RR header (8B): V=2 RC=0 PT=201 len=1
81 ca 00 05 <SSRC>                       # SDES header (4B): V=2 SC=1 PT=202 len=5
01 0d <multicast-group-as-string> 00     # CNAME chunk: type=1 len=13 value+null
                                          # padding to a 4-byte boundary
```

Total: 32 bytes.

| Field | Value |
|---|---|
| RTCP RR PT | 201 |
| RTCP SDES PT | 202 |
| **CNAME value** | **The multicast group address as an ASCII string** (e.g. `"239.255.255.1"`) |
| Send interval | 5 seconds |

## PTT signaling

VE-PG4 does not use the RTP marker bit. PTT start/end is inferred:

- **PTT start (radio RX)**: an RTP stream begins (first packet after a quiet period).
- **PTT end**: the RTP stream stops (~200 ms+ of silence).

The bridge auto-releases the PTT gate after 800 ms of idle.

## VE-PG4 routing prerequisite

> **Important**: the RoIP Gateway port must be a member of the SelCall group (in `Destination Settings`) used by your radios. Without this, multicast packets reach the device but audio is not routed between the IP layer and the radio (no PTT keying or audio path).

Where to check in the web UI:
- `Destination Settings → SelCall Number Converting`, or
- `Destination Settings → Destination Settings`
- Add the RoIP Gateway port (e.g. 1) to the group's member list.

## Verification

- **0% packet loss** — VE-PG4's RR `fraction lost` field reads 0 when receiving our RTP, confirming all sent packets were received.
- **Byte-for-byte RTCP compatibility** — `build_rtcp_rr_sdes()` produces a byte sequence identical to VE-PG4's outgoing RTCP.

## Module mapping

| Convention | Implementation |
|---|---|
| Multicast group / port | `config.py` |
| IGMP join + outgoing iface | `multicast.py` |
| G.711μ encode/decode | `codec.py` |
| 8k ↔ 48k mono resample | `codec.py` |
| 48k mono ↔ 48k stereo | `codec.py` |
| RTP 12-byte header | `rtp.py` |
| RTCP RR + SDES | `rtp.py` |
| 5 s keepalive | `bridge.py` (`_rtcp_keepalive_loop`) |
| FCFS PTT | `ptt.py`, `bridge.py` |

## References

- RFC 3550 — RTP / RTCP
- RFC 3551 — RTP profile (G.711 PT=0)
- ITU-T G.711 — μ-law encoding
