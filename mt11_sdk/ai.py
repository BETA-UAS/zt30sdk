"""Client for UniPod MT11 built-in AI tracking."""

from __future__ import annotations

from dataclasses import dataclass
import socket
import struct
import threading
import time
from typing import Callable, Optional

from .constants import DEFAULT_AI_IP, DEFAULT_PORT
from .protocol import MT11Packet, build_packet, parse_packets


AI_TARGET_TYPES = {
    0: "people",
    1: "car",
    2: "bus",
    3: "truck",
    255: "arbitrary",
}

AI_TRACK_STATES = {
    0: "normal_ai",
    1: "intermittent_loss",
    2: "lost",
    3: "cancelled",
    4: "normal_arbitrary",
}


@dataclass(frozen=True)
class AITrackingBox:
    """Tracking target rectangle reported by the MT11."""

    x: int
    y: int
    width: int
    height: int
    target_id: int
    track_state: int

    @property
    def target_type(self) -> str:
        return AI_TARGET_TYPES.get(self.target_id, f"unknown_{self.target_id}")

    @property
    def state(self) -> str:
        return AI_TRACK_STATES.get(self.track_state, f"unknown_{self.track_state}")

    @property
    def left(self) -> int:
        return int(self.x - self.width / 2)

    @property
    def top(self) -> int:
        return int(self.y - self.height / 2)

    @property
    def right(self) -> int:
        return int(self.x + self.width / 2)

    @property
    def bottom(self) -> int:
        return int(self.y + self.height / 2)


class MT11AITrackingClient:
    """
    UniPod MT11 AI tracking client.

    MT11 AI tracking is built into the gimbal camera and uses the same
    192.168.144.25:37260 SDK endpoint as the rest of the camera controls.
    """

    def __init__(
        self,
        host: str = DEFAULT_AI_IP,
        port: int = DEFAULT_PORT,
        timeout: float = 0.5,
        validate_crc: bool = True,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.validate_crc = validate_crc
        self.seq = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(timeout)
        self._io_lock = threading.Lock()
        self._listen_thread: Optional[threading.Thread] = None
        self._listening = False
        self.last_mode_error: Optional[int] = None
        self.last_select_error: Optional[int] = None

    def close(self) -> None:
        self.stop_coordinate_listener()
        self.sock.close()

    def set_target(self, host: str, port: int = DEFAULT_PORT) -> None:
        self.host = host
        self.port = port

    def _next_seq(self) -> int:
        value = self.seq & 0xFFFF
        self.seq = (self.seq + 1) & 0xFFFF
        return value

    def send(
        self,
        cmd_id: int,
        payload: bytes = b"",
        wait_response: bool = True,
        need_ack: bool = True,
    ) -> Optional[MT11Packet]:
        with self._io_lock:
            packet = build_packet(cmd_id, payload, seq=self._next_seq(), need_ack=need_ack)
            self.sock.sendto(packet, (self.host, self.port))
            if not wait_response:
                return None

            deadline = time.monotonic() + self.timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.sock.settimeout(remaining)
                try:
                    raw, _ = self.sock.recvfrom(4096)
                except socket.timeout:
                    return None
                finally:
                    self.sock.settimeout(self.timeout)
                for pkt in parse_packets(raw, validate_crc=self.validate_crc):
                    if pkt.cmd_id == cmd_id:
                        return pkt

    def request_firmware_version(self) -> Optional[str]:
        pkt = self.send(0x01)
        if not pkt or len(pkt.payload) < 4:
            return None
        value = struct.unpack("<I", pkt.payload[:4])[0]
        return self._decode_firmware(value)

    @staticmethod
    def _decode_firmware(value: int) -> str:
        return f"v{(value >> 16) & 0xFF}.{(value >> 8) & 0xFF}.{value & 0xFF}"

    def get_recognition_enabled(self) -> Optional[bool]:
        pkt = self.send(0x4D)
        if not pkt or len(pkt.payload) < 1:
            return None
        return pkt.payload[0] == 1

    def set_recognition_enabled(self, enabled: bool) -> Optional[bool]:
        pkt = self.send(0x55, struct.pack("<B", 1 if enabled else 0))
        if not pkt or len(pkt.payload) < 1:
            return None
        self.last_mode_error = pkt.payload[1] if len(pkt.payload) > 1 else None
        return pkt.payload[0] == (1 if enabled else 0) and self.last_mode_error in (None, 0)

    def get_tracking_status(self) -> Optional[bool]:
        pkt = self.send(0x57)
        if not pkt or len(pkt.payload) < 1:
            return None
        return pkt.payload[0] == 0

    def track_point(self, x: int, y: int) -> Optional[int]:
        payload = struct.pack("<BHHHH", 1, self._clamp_u16(x), self._clamp_u16(y), 0, 0)
        pkt = self.send(0x56, payload)
        return self._select_status(pkt)

    def track_box(self, left: int, top: int, right: int, bottom: int) -> Optional[int]:
        payload = struct.pack(
            "<BHHHH",
            1,
            self._clamp_u16(left),
            self._clamp_u16(top),
            self._clamp_u16(right),
            self._clamp_u16(bottom),
        )
        pkt = self.send(0x56, payload)
        return self._select_status(pkt)

    def cancel_tracking(self) -> Optional[int]:
        pkt = self.send(0x56, struct.pack("<BHHHH", 0, 0, 0, 0, 0))
        return self._select_status(pkt)

    def set_tracking_target(self, enabled: bool, lx: int, ly: int, rx: int, ry: int, action: int = 1) -> Optional[int]:
        payload = struct.pack(
            "<BHHHH",
            action if enabled else 0,
            self._clamp_u16(lx),
            self._clamp_u16(ly),
            self._clamp_u16(rx),
            self._clamp_u16(ry),
        )
        pkt = self.send(0x56, payload)
        return self._select_status(pkt)

    def get_coordinate_stream_status(self) -> Optional[int]:
        pkt = self.send(0x4E)
        if not pkt or len(pkt.payload) < 1:
            return None
        return pkt.payload[0]

    def set_coordinate_stream_enabled(self, enabled: bool) -> Optional[bool]:
        pkt = self.send(0x51, struct.pack("<B", 1 if enabled else 0))
        if not pkt or len(pkt.payload) < 1:
            return None
        return pkt.payload[0] == (1 if enabled else 0)

    def set_rtsp_stream_enabled(self, enabled: bool) -> Optional[bool]:
        return True

    def start_coordinate_listener(
        self,
        callback: Callable[[AITrackingBox], None],
        error_callback: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        if self._listen_thread and self._listen_thread.is_alive():
            return

        self._listening = True
        self._listen_thread = threading.Thread(
            target=self._coordinate_listener_worker,
            args=(callback, error_callback),
            daemon=True,
        )
        self._listen_thread.start()

    def stop_coordinate_listener(self) -> None:
        self._listening = False
        if self._listen_thread and self._listen_thread.is_alive():
            self._listen_thread.join(timeout=1.0)
        self._listen_thread = None

    def _coordinate_listener_worker(
        self,
        callback: Callable[[AITrackingBox], None],
        error_callback: Optional[Callable[[Exception], None]],
    ) -> None:
        while self._listening:
            try:
                if not self._io_lock.acquire(blocking=False):
                    time.sleep(0.02)
                    continue
                try:
                    self.sock.settimeout(0.05)
                    try:
                        raw, _ = self.sock.recvfrom(4096)
                    except socket.timeout:
                        continue
                    finally:
                        self.sock.settimeout(self.timeout)
                finally:
                    self._io_lock.release()
                for pkt in parse_packets(raw, validate_crc=self.validate_crc):
                    if pkt.cmd_id == 0x50:
                        box = self.parse_tracking_box(pkt.payload)
                        callback(box)
            except Exception as exc:
                if error_callback:
                    error_callback(exc)

    @staticmethod
    def parse_tracking_box(payload: bytes) -> AITrackingBox:
        if len(payload) < 10:
            raise ValueError(f"AI tracking payload too short: {len(payload)} bytes")
        x, y, width, height, target_id, track_state = struct.unpack("<HHHHBB", payload[:10])
        return AITrackingBox(x, y, width, height, target_id, track_state)

    def _select_status(self, pkt: Optional[MT11Packet]) -> Optional[int]:
        if not pkt or len(pkt.payload) < 1:
            return None
        self.last_select_error = pkt.payload[0]
        return pkt.payload[0]

    @staticmethod
    def _clamp_u16(value: int) -> int:
        return max(0, min(65535, int(value)))
