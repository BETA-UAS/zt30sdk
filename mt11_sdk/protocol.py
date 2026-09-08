"""
UniPod MT11 gimbal camera SDK protocol helpers.

Packet format:
  STX      2 bytes: 55 66
  CTRL     1 byte
  DATA_LEN 2 bytes little endian
  SEQ      2 bytes little endian
  CMD_ID   1 byte
  DATA     n bytes
  CRC16    2 bytes little endian
"""

from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import List, Optional

STX = b"\x55\x66"


class MT11ProtocolError(Exception):
    """Raised when an SDK packet is malformed or fails CRC validation."""


@dataclass(frozen=True)
class MT11Packet:
    ctrl: int
    data_len: int
    seq: int
    cmd_id: int
    payload: bytes
    crc: int
    raw: bytes

    @property
    def need_ack(self) -> bool:
        return bool(self.ctrl & 0x01)

    @property
    def is_ack(self) -> bool:
        return bool(self.ctrl & 0x02)


def crc16_ccitt(data: bytes, crc_init: int = 0) -> int:
    """
    CRC16 implementation compatible with the MT11 SDK examples.
    Polynomial: 0x1021
    Initial value: 0x0000
    Output is returned as integer. Pack it little endian in the frame.
    """
    crc = crc_init & 0xFFFF
    for byte in data:
        temp = ((crc >> 8) ^ byte) & 0xFF
        temp ^= (temp >> 4)
        crc = (
            ((crc << 8) & 0xFFFF)
            ^ ((temp << 12) & 0xFFFF)
            ^ ((temp << 5) & 0xFFFF)
            ^ temp
        ) & 0xFFFF
    return crc & 0xFFFF


def build_packet(cmd_id: int, payload: bytes = b"", seq: int = 0, need_ack: bool = True, ack_pack: bool = False) -> bytes:
    ctrl = 0
    if need_ack:
        ctrl |= 0x01
    if ack_pack:
        ctrl |= 0x02

    header = struct.pack("<2sBHHB", STX, ctrl, len(payload), seq & 0xFFFF, cmd_id & 0xFF)
    body = header + payload
    crc = crc16_ccitt(body)
    return body + struct.pack("<H", crc)


def parse_packet(raw: bytes, validate_crc: bool = True) -> MT11Packet:
    if len(raw) < 10:
        raise MT11ProtocolError(f"Packet too short: {len(raw)} bytes")
    if raw[0:2] != STX:
        raise MT11ProtocolError(f"Invalid STX: {raw[0:2].hex(' ')}")

    ctrl, data_len, seq, cmd_id = struct.unpack("<BHHB", raw[2:8])
    expected_len = 8 + data_len + 2
    if len(raw) < expected_len:
        raise MT11ProtocolError(f"Incomplete packet: got {len(raw)}, expected {expected_len}")

    raw = raw[:expected_len]
    payload = raw[8:8 + data_len]
    crc_rx = struct.unpack("<H", raw[8 + data_len:10 + data_len])[0]

    if validate_crc:
        crc_calc = crc16_ccitt(raw[:-2])
        if crc_rx != crc_calc:
            raise MT11ProtocolError(f"CRC mismatch: rx=0x{crc_rx:04X}, calc=0x{crc_calc:04X}")

    return MT11Packet(ctrl=ctrl, data_len=data_len, seq=seq, cmd_id=cmd_id, payload=payload, crc=crc_rx, raw=raw)


def parse_packets(raw: bytes, validate_crc: bool = True) -> List[MT11Packet]:
    """Parse one or more SDK packets from a single byte buffer."""
    packets = []
    offset = 0

    while offset < len(raw):
        stx_at = raw.find(STX, offset)
        if stx_at < 0:
            break
        if len(raw) - stx_at < 10:
            break

        data_len = struct.unpack("<H", raw[stx_at + 3:stx_at + 5])[0]
        packet_len = 8 + data_len + 2
        if len(raw) - stx_at < packet_len:
            break

        packets.append(parse_packet(raw[stx_at:stx_at + packet_len], validate_crc=validate_crc))
        offset = stx_at + packet_len

    return packets


def hexdump(data: Optional[bytes]) -> str:
    if data is None:
        return "NO RESPONSE"
    return " ".join(f"{b:02X}" for b in data)
