"""RTP / RTCP build + parse — VE-PG4 (ICOM RoIP Gateway) compatible."""
import struct
from dataclasses import dataclass


@dataclass
class RtpFrame:
    version: int
    marker: bool
    pt: int
    seq: int
    timestamp: int
    ssrc: int
    payload: bytes


def build_rtp(seq: int, ts: int, payload: bytes, ssrc: int,
              marker: bool = False, pt: int = 0) -> bytes:
    b0 = (2 << 6)
    b1 = ((1 << 7) if marker else 0) | (pt & 0x7F)
    return struct.pack("!BBHII", b0, b1, seq & 0xFFFF, ts & 0xFFFFFFFF, ssrc & 0xFFFFFFFF) + payload


def parse_rtp(data: bytes):
    if len(data) < 12:
        return None
    b0, b1, seq, ts, ssrc = struct.unpack_from("!BBHII", data, 0)
    if (b0 >> 6) != 2:
        return None
    cc = b0 & 0x0F
    pt = b1 & 0x7F
    marker = bool(b1 >> 7 & 1)
    hdr_len = 12 + cc * 4
    return RtpFrame(2, marker, pt, seq, ts, ssrc, data[hdr_len:])


def build_rtcp_rr_sdes(ssrc: int, cname: bytes) -> bytes:
    """RR + SDES — byte-for-byte compatible with VE-PG4's 32-byte RTCP."""
    rr = struct.pack("!BBHI", 0x80, 201, 1, ssrc)
    cname_item = bytes([1, len(cname)]) + cname + b"\x00"
    chunk = struct.pack("!I", ssrc) + cname_item
    while len(chunk) % 4 != 0:
        chunk += b"\x00"
    sdes_len_words = len(chunk) // 4
    sdes = struct.pack("!BBH", 0x81, 202, sdes_len_words) + chunk
    return rr + sdes
