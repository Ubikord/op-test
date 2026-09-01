"""
Общий протокол обмена между Master и Slave-агентом.
protocol.py
"""
from __future__ import annotations

import json
import socket
import struct
from dataclasses import dataclass, field
from typing import Any, Optional, List

MAGIC_ETHERTYPE = 0x88B5
DEFAULT_AGENT_PORT = 5959
RECV_TIMEOUT_S = 10.0


class ProtocolError(Exception):
    pass


def send_message(sock: socket.socket, message: dict) -> None:
    payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
    header = struct.pack(">I", len(payload))
    sock.sendall(header + payload)


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ProtocolError("Соединение закрыто удалённой стороной")
        buf.extend(chunk)
    return bytes(buf)


def recv_message(sock: socket.socket) -> dict:
    header = recv_exact(sock, 4)
    (length,) = struct.unpack(">I", header)
    if length > 16 * 1024 * 1024:
        raise ProtocolError(f"Слишком большое сообщение: {length} байт")
    payload = recv_exact(sock, length)
    return json.loads(payload.decode("utf-8"))


def request(host: str, port: int, message: dict, timeout: float = RECV_TIMEOUT_S) -> dict:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        send_message(sock, message)
        return recv_message(sock)


@dataclass
class EndpointRef:
    """Ссылка на интерфейс агента."""
    slave: str
    host: str
    iface: str
    mac: str
    vlans: List[int] = field(default_factory=list)

@dataclass
class TestSpec:
    test_id: str
    role: str                 # "sender" | "receiver"
    iface: str
    dst_mac: Optional[str] = None
    src_mac: Optional[str] = None
    size_mode: str = "fixed"
    size: int = 512
    size_min: int = 64
    size_max: int = 1500
    rate_pps: int = 1000
    duration_s: float = 5.0
    packet_count: Optional[int] = None

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        return {k: v for k, v in d.items() if v is not None}

    @staticmethod
    def from_dict(d: dict) -> "TestSpec":
        fields = set(TestSpec.__dataclass_fields__)
        return TestSpec(**{k: d[k] for k in fields if k in d})