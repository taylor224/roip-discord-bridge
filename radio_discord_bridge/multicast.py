"""Multicast socket helper — IGMP join + outgoing interface selection."""
import socket
import struct
from typing import Optional


def open_multicast_socket(group: str, port: int, iface_ip: Optional[str] = None,
                          ttl: int = 32) -> socket.socket:
    sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    sk.bind(("", port))

    if iface_ip:
        mreq = struct.pack("4s4s", socket.inet_aton(group), socket.inet_aton(iface_ip))
    else:
        mreq = struct.pack("4sl", socket.inet_aton(group), socket.INADDR_ANY)
    sk.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    sk.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, ttl)
    if iface_ip:
        sk.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(iface_ip))
    sk.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 0)

    sk.setblocking(False)
    return sk
