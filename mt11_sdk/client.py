"""High level UDP client for UniPod MT11 gimbal camera."""

from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .constants import (
    DEFAULT_IP,
    DEFAULT_PORT,
    IMAGE_MODE_BY_NAME,
    IMAGE_MODES,
    MT11_STREAM_NAMES,
    PHOTO_RECORD_FUNC,
    STREAM_TYPE_BY_NAME,
    THERMAL_PALETTE_BY_NAME,
    THERMAL_PALETTES,
    VIDEO_ENCODER_BY_NAME,
)
from .protocol import MT11Packet, build_packet, hexdump, parse_packet


@dataclass
class CodecSpec:
    stream_type: int
    encoder: int
    width: int
    height: int
    bitrate_kbps: int
    frame_rate: Optional[int] = None


class MT11UDPClient:
    """
    UniPod MT11 UDP SDK client.

    This class implements the commands documented in the UniPod MT11 External
    SDK Protocol Specification.
    """

    def __init__(self, host: str = DEFAULT_IP, port: int = DEFAULT_PORT, timeout: float = 0.5, validate_crc: bool = True):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.validate_crc = validate_crc
        self.seq = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(timeout)
        self._io_lock = threading.Lock()

    def close(self) -> None:
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
        response_cmd_id: Optional[int] = None,
    ) -> Optional[MT11Packet]:
        with self._io_lock:
            seq = self._next_seq()
            packet = build_packet(cmd_id, payload, seq=seq, need_ack=need_ack)
            self.sock.sendto(packet, (self.host, self.port))
            if not wait_response:
                return None

            # Function feedback (0x0B) and delayed UDP replies may already be
            # queued. Do not let one of those packets masquerade as this
            # command's response.
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

                response = parse_packet(raw, validate_crc=self.validate_crc)
                expected_cmd_id = cmd_id if response_cmd_id is None else response_cmd_id
                if response.cmd_id != expected_cmd_id:
                    continue
                # MT11 firmware maintains its own response sequence counter;
                # it does not echo the request sequence. CMD_ID is the stable
                # correlation key for this request/response protocol.
                return response

    def send_raw_hex(self, hex_string: str, wait_response: bool = True) -> Optional[MT11Packet]:
        with self._io_lock:
            raw = bytes.fromhex(hex_string)
            self.sock.sendto(raw, (self.host, self.port))
            if not wait_response:
                return None
            try:
                response, _ = self.sock.recvfrom(4096)
            except socket.timeout:
                return None
            return parse_packet(response, validate_crc=self.validate_crc)

    # General information

    def request_firmware_version(self) -> Optional[Dict[str, str]]:
        pkt = self.send(0x01)
        if not pkt or len(pkt.payload) < 8:
            return None
        payload = pkt.payload[:12].ljust(12, b"\x00")
        values = struct.unpack("<III", payload)
        return {
            "camera": self._decode_firmware(values[0]),
            "gimbal": self._decode_firmware(values[1]),
            "zoom": None,
            "raw": values,
        }

    @staticmethod
    def _decode_firmware(value: int) -> str:
        # Manual example: 0x6E030203 means v3.2.3 and high byte is ignored.
        return f"v{(value >> 16) & 0xFF}.{(value >> 8) & 0xFF}.{value & 0xFF}"

    def request_hardware_id(self) -> Optional[str]:
        pkt = self.send(0x02)
        if not pkt:
            return None
        return pkt.payload.rstrip(b"\x00").decode("ascii", errors="replace")

    def request_working_mode(self) -> Optional[str]:
        pkt = self.send(0x19)
        if not pkt or len(pkt.payload) < 1:
            return None
        return {0: "lock", 1: "follow", 2: "fpv"}.get(pkt.payload[0], f"unknown_{pkt.payload[0]}")

    def request_config(self) -> Optional[Dict[str, Any]]:
        pkt = self.send(0x0A)
        if not pkt:
            return None
        p = pkt.payload
        result = {"raw": p}
        if len(p) >= 7:
            result.update({
                "hdr": {0: "off", 1: "on"}.get(p[1], p[1]),
                "record": {0: "off", 1: "on", 2: "tf_empty", 3: "tf_data_loss"}.get(p[3], p[3]),
                "motion_mode": {0: "lock", 1: "follow", 2: "fpv"}.get(p[4], p[4]),
                "mounting": {0: "reserved", 1: "normal", 2: "upside_down"}.get(p[5], p[5]),
                "video_output": p[6],
            })
        if len(p) >= 8:
            result["zoom_linkage"] = {0: "off", 1: "on"}.get(p[7], p[7])
        return result

    def request_function_feedback(self) -> Optional[Dict[str, Any]]:
        pkt = self.send(0x0B)
        if not pkt or len(pkt.payload) < 1:
            return None
        mapping = {
            0: "photo_success",
            1: "fail_photo_check_tf",
            2: "hdr_on",
            3: "hdr_off",
            4: "fail_record_check_tf",
            5: "recording_started",
            6: "recording_stopped",
        }
        return {"info_type": pkt.payload[0], "message": mapping.get(pkt.payload[0], "unknown")}

    # Gimbal movement

    def rotate_speed(self, yaw: int, pitch: int) -> Optional[bool]:
        yaw = max(-100, min(100, int(yaw)))
        pitch = max(-100, min(100, int(pitch)))
        pkt = self.send(0x07, struct.pack("<bb", yaw, pitch))
        return self._ack_success(pkt)

    def stop_rotation(self) -> Optional[bool]:
        return self.rotate_speed(0, 0)

    def center(self) -> Optional[bool]:
        pkt = self.send(0x08, struct.pack("<B", 1))
        return self._ack_success(pkt)

    def set_angle(self, yaw_deg: float, pitch_deg: float) -> Optional[Dict[str, float]]:
        yaw_raw = int(round(yaw_deg * 10.0))
        pitch_raw = int(round(pitch_deg * 10.0))
        pkt = self.send(0x0E, struct.pack("<hh", yaw_raw, pitch_raw))
        if not pkt or len(pkt.payload) < 6:
            return None
        yaw, pitch, roll = struct.unpack("<hhh", pkt.payload[:6])
        return {"yaw_deg": yaw / 10.0, "pitch_deg": pitch / 10.0, "roll_deg": roll / 10.0}

    def request_attitude(self) -> Optional[Dict[str, float]]:
        pkt = self.send(0x0D)
        if not pkt or len(pkt.payload) < 12:
            return None
        yaw, pitch, roll, yv, pv, rv = struct.unpack("<hhhhhh", pkt.payload[:12])
        return {
            "yaw_deg": yaw / 10.0,
            "pitch_deg": pitch / 10.0,
            "roll_deg": roll / 10.0,
            "yaw_velocity_deg_s": yv / 10.0,
            "pitch_velocity_deg_s": pv / 10.0,
            "roll_velocity_deg_s": rv / 10.0,
        }

    def single_axis_control(self, angle_deg: float, axis: str = "yaw") -> Optional[Dict[str, float]]:
        # Mostly for A8 mini, included for SDK completeness.
        flag = 0 if axis.lower() == "yaw" else 1
        pkt = self.send(0x41, struct.pack("<hB", int(round(angle_deg * 10.0)), flag))
        if not pkt or len(pkt.payload) < 6:
            return None
        yaw, pitch, roll = struct.unpack("<hhh", pkt.payload[:6])
        return {"yaw_deg": yaw / 10.0, "pitch_deg": pitch / 10.0, "roll_deg": roll / 10.0}

    # Camera optical functions

    def auto_focus(self, touch_x: int = 0, touch_y: int = 0) -> Optional[bool]:
        pkt = self.send(0x04, struct.pack("<BHH", 1, int(touch_x), int(touch_y)))
        return self._ack_success(pkt)

    def manual_zoom(self, direction: int) -> Optional[int]:
        # direction: 1 zoom in, 0 stop, -1 zoom out
        direction = 1 if direction > 0 else -1 if direction < 0 else 0
        pkt = self.send(0x05, struct.pack("<b", direction))
        if not pkt or len(pkt.payload) < 2:
            return None
        return struct.unpack("<H", pkt.payload[:2])[0]

    def zoom_in(self) -> Optional[int]:
        return self.manual_zoom(1)

    def zoom_out(self) -> Optional[int]:
        return self.manual_zoom(-1)

    def zoom_stop(self) -> Optional[int]:
        return self.manual_zoom(0)

    def absolute_zoom(self, zoom: float) -> Optional[bool]:
        zoom = max(1.0, min(30.9, float(zoom)))
        integer = int(zoom)
        fractional = int(round((zoom - integer) * 10))
        if fractional > 9:
            integer += 1
            fractional = 0
        pkt = self.send(0x0F, struct.pack("<BB", integer, fractional))
        return self._ack_success(pkt)

    def request_max_zoom(self) -> Optional[float]:
        pkt = self.send(0x16)
        if not pkt or len(pkt.payload) < 2:
            return None
        return pkt.payload[0] + pkt.payload[1] / 10.0

    def request_zoom(self) -> Optional[float]:
        pkt = self.send(0x18)
        if not pkt or len(pkt.payload) < 2:
            return None
        return pkt.payload[0] + pkt.payload[1] / 10.0

    def manual_focus(self, direction: int) -> Optional[bool]:
        # 1 long shot, 0 stop, -1 close shot
        direction = 1 if direction > 0 else -1 if direction < 0 else 0
        pkt = self.send(0x06, struct.pack("<b", direction))
        return self._ack_success(pkt)

    def focus_far(self) -> Optional[bool]:
        return self.manual_focus(1)

    def focus_near(self) -> Optional[bool]:
        return self.manual_focus(-1)

    def focus_stop(self) -> Optional[bool]:
        return self.manual_focus(0)

    # Photo, record, modes

    def camera_function(self, func: int | str, wait_response: bool = False) -> Optional[MT11Packet]:
        if isinstance(func, str):
            func_id = PHOTO_RECORD_FUNC[func]
        else:
            func_id = int(func)
        # CMD 0x0C is explicitly documented as a no-ACK command.
        return self.send(
            0x0C,
            struct.pack("<B", func_id),
            wait_response=wait_response,
            need_ack=False,
        )

    def take_photo(self) -> None:
        self.camera_function("photo", wait_response=False)

    def toggle_record(self) -> Optional[Dict[str, Any]]:
        pkt = self.send(
            0x0C,
            struct.pack("<B", PHOTO_RECORD_FUNC["record_toggle"]),
            wait_response=True,
            need_ack=False,
            response_cmd_id=0x0B,
        )
        if not pkt or not pkt.payload:
            return None
        info_type = pkt.payload[0]
        mapping = {
            4: "fail_record_check_tf",
            5: "recording_started",
            6: "recording_stopped",
        }
        return {"info_type": info_type, "message": mapping.get(info_type, f"feedback_{info_type}")}

    def set_motion_mode(self, mode: str) -> None:
        mode = mode.lower().strip()
        if mode not in ("lock", "follow", "fpv"):
            raise ValueError("mode must be lock, follow, or fpv")
        self.camera_function(f"{mode}_mode", wait_response=False)

    # Codec and image mode

    def request_codec_specs(self, stream_type: int | str = 1) -> Optional[CodecSpec]:
        st = STREAM_TYPE_BY_NAME.get(stream_type, stream_type) if isinstance(stream_type, str) else int(stream_type)
        pkt = self.send(0x20, struct.pack("<B", st))
        if not pkt or len(pkt.payload) < 9:
            return None
        stream, enc, width, height, bitrate, frame_rate = struct.unpack("<BBHHHB", pkt.payload[:9])
        return CodecSpec(stream, enc, width, height, bitrate, frame_rate)

    def set_codec_specs(self, stream_type: int | str, encoder: int | str, width: int, height: int, bitrate_kbps: int) -> Optional[bool]:
        st = STREAM_TYPE_BY_NAME.get(stream_type, stream_type) if isinstance(stream_type, str) else int(stream_type)
        enc = VIDEO_ENCODER_BY_NAME.get(encoder, encoder) if isinstance(encoder, str) else int(encoder)
        payload = struct.pack("<BBHHHB", st, enc, int(width), int(height), int(bitrate_kbps), 0)
        pkt = self.send(0x21, payload)
        if not pkt or len(pkt.payload) < 2:
            return None
        return bool(pkt.payload[1])

    def request_image_mode(self) -> Optional[str]:
        pkt = self.send(0x10)
        if not pkt or len(pkt.payload) < 2:
            return None
        mode = (pkt.payload[0], pkt.payload[1])
        return IMAGE_MODES.get(mode, f"{MT11_STREAM_NAMES.get(mode[0], mode[0])}_sub_{MT11_STREAM_NAMES.get(mode[1], mode[1])}")

    def set_image_mode(self, mode: tuple[int, int] | str) -> Optional[str]:
        if isinstance(mode, str):
            mode_pair = IMAGE_MODE_BY_NAME[mode]
        else:
            mode_pair = (int(mode[0]), int(mode[1]))
        pkt = self.send(0x11, struct.pack("<BB", mode_pair[0], mode_pair[1]))
        if not pkt or len(pkt.payload) < 2:
            return None
        response = (pkt.payload[0], pkt.payload[1])
        return IMAGE_MODES.get(response, f"{MT11_STREAM_NAMES.get(response[0], response[0])}_sub_{MT11_STREAM_NAMES.get(response[1], response[1])}")

    # Thermal functions

    def request_temperature_point(self, x: int, y: int, flag: int = 1) -> Optional[Dict[str, Any]]:
        pkt = self.send(0x12, struct.pack("<HHB", int(x), int(y), int(flag)))
        if not pkt or len(pkt.payload) < 6:
            return None
        temp, rx, ry = struct.unpack("<HHH", pkt.payload[:6])
        return {"temperature_c": temp / 100.0, "x": rx, "y": ry}

    def request_temperature_box(self, startx: int, starty: int, endx: int, endy: int, flag: int = 1) -> Optional[Dict[str, Any]]:
        payload = struct.pack("<HHHHB", int(startx), int(starty), int(endx), int(endy), int(flag))
        pkt = self.send(0x13, payload)
        if not pkt or len(pkt.payload) < 20:
            return None
        vals = struct.unpack("<HHHHHHHHHH", pkt.payload[:20])
        return {
            "startx": vals[0], "starty": vals[1], "endx": vals[2], "endy": vals[3],
            "temp_max_c": vals[4] / 100.0, "temp_min_c": vals[5] / 100.0,
            "temp_max_x": vals[6], "temp_max_y": vals[7], "temp_min_x": vals[8], "temp_min_y": vals[9],
        }

    def request_temperature_full_image(self, flag: int = 1) -> Optional[Dict[str, Any]]:
        pkt = self.send(0x14, struct.pack("<B", int(flag)))
        if not pkt or len(pkt.payload) < 12:
            return None
        vals = struct.unpack("<HHHHHH", pkt.payload[:12])
        return {
            "temp_max_c": vals[0] / 100.0, "temp_min_c": vals[1] / 100.0,
            "temp_max_x": vals[2], "temp_max_y": vals[3], "temp_min_x": vals[4], "temp_min_y": vals[5],
        }

    def request_thermal_palette(self) -> Optional[str]:
        pkt = self.send(0x1A)
        if not pkt or len(pkt.payload) < 1:
            return None
        return THERMAL_PALETTES.get(pkt.payload[0], f"unknown_{pkt.payload[0]}")

    def set_thermal_palette(self, palette: int | str) -> Optional[str]:
        pid = THERMAL_PALETTE_BY_NAME.get(palette, palette) if isinstance(palette, str) else int(palette)
        pkt = self.send(0x1B, struct.pack("<B", pid))
        if not pkt or len(pkt.payload) < 1:
            return None
        return THERMAL_PALETTES.get(pkt.payload[0], f"unknown_{pkt.payload[0]}")

    def request_thermal_gain(self) -> Optional[str]:
        pkt = self.send(0x37)
        if not pkt or len(pkt.payload) < 1:
            return None
        return "high" if pkt.payload[0] == 1 else "low"

    def set_thermal_gain(self, high: bool) -> Optional[str]:
        pkt = self.send(0x38, struct.pack("<B", 1 if high else 0))
        if not pkt or len(pkt.payload) < 1:
            return None
        return "high" if pkt.payload[0] == 1 else "low"

    # Laser rangefinder

    def request_laser_range(self) -> Optional[float]:
        pkt = self.send(0x15)
        if not pkt or len(pkt.payload) < 2:
            return None
        dm = struct.unpack("<H", pkt.payload[:2])[0]
        return dm / 10.0

    def request_laser_target_latlon(self) -> Optional[Dict[str, float]]:
        pkt = self.send(0x17)
        if not pkt or len(pkt.payload) < 8:
            return None
        lon, lat = struct.unpack("<ii", pkt.payload[:8])
        return {"lat": lat / 1e7, "lon": lon / 1e7}

    def request_laser_status(self) -> Optional[bool]:
        pkt = self.send(0x31)
        if not pkt or len(pkt.payload) < 1:
            return None
        return pkt.payload[0] == 1

    def set_laser(self, enable: bool) -> Optional[bool]:
        pkt = self.send(0x32, struct.pack("<B", 1 if enable else 0))
        return self._ack_success(pkt)

    # Flight controller integration and streams

    def send_flight_controller_attitude(self, roll: float, pitch: float, yaw: float, rollspeed: float = 0.0, pitchspeed: float = 0.0, yawspeed: float = 0.0, time_boot_ms: Optional[int] = None) -> Optional[MT11Packet]:
        if time_boot_ms is None:
            time_boot_ms = int(time.monotonic() * 1000)
        payload = struct.pack("<Iffffff", int(time_boot_ms), float(roll), float(pitch), float(yaw), float(rollspeed), float(pitchspeed), float(yawspeed))
        return self.send(0x22, payload)

    def request_fc_data_stream_to_gimbal(self, data_type: int = 1, data_freq: int = 4) -> Optional[int]:
        pkt = self.send(0x24, struct.pack("<BB", int(data_type), int(data_freq)))
        if not pkt or len(pkt.payload) < 1:
            return None
        return pkt.payload[0]

    def send_flight_controller_gps(self, time_boot_ms: int, lat_deg_e7: int, lon_deg_e7: int, alt_cm: int, alt_ellipsoid_cm: int, vn_mps: float, ve_mps: float, vd_mps: float) -> Optional[MT11Packet]:
        payload = struct.pack(
            "<Iiiiiiii",
            int(time_boot_ms),
            int(lat_deg_e7),
            int(lon_deg_e7),
            int(alt_cm),
            int(alt_ellipsoid_cm),
            int(round(vn_mps * 1000)),
            int(round(ve_mps * 1000)),
            int(round(vd_mps * 1000)),
        )
        return self.send(0x3E, payload)

    def request_gimbal_data_stream(self, data_type: int = 1, data_freq: int = 4) -> Optional[int]:
        pkt = self.send(0x25, struct.pack("<BB", int(data_type), int(data_freq)))
        if not pkt or len(pkt.payload) < 1:
            return None
        return pkt.payload[0]

    # Maintenance

    def set_utc_time(self, timestamp_us: Optional[int] = None) -> Optional[bool]:
        if timestamp_us is None:
            timestamp_us = int(time.time() * 1_000_000)
        pkt = self.send(0x30, struct.pack("<Q", int(timestamp_us)))
        return self._ack_success(pkt)

    def request_system_time(self) -> Optional[Dict[str, int]]:
        pkt = self.send(0x40)
        if not pkt or len(pkt.payload) < 12:
            return None
        unix_us, boot_ms = struct.unpack("<QI", pkt.payload[:12])
        return {"unix_us": unix_us, "boot_ms": boot_ms}

    def format_sd_card(self) -> Optional[bool]:
        pkt = self.send(0x48)
        return self._ack_success(pkt)

    def request_tf_card_info(self) -> Optional[Dict[str, Any]]:
        pkt = self.send(0x49)
        if not pkt or len(pkt.payload) < 6:
            return None
        status, fs, total, available = struct.unpack("<BBHH", pkt.payload[:6])
        return {
            "status": status,
            "file_system": fs,
            "total_gb": total / 100.0,
            "available_gb": available / 100.0,
        }

    def manual_thermal_shutter(self) -> Optional[bool]:
        pkt = self.send(0x4F)
        return self._ack_success(pkt)

    def get_defog_mode(self) -> Optional[int]:
        pkt = self.send(0x62, b"")
        if not pkt or len(pkt.payload) < 1:
            return None
        return pkt.payload[0]

    def set_defog_mode(self, mode: int) -> Optional[int]:
        pkt = self.send(0x62, struct.pack("<B", int(mode)))
        if not pkt or len(pkt.payload) < 1:
            return None
        return pkt.payload[0]

    def get_night_vision(self) -> Optional[bool]:
        pkt = self.send(0x63, b"")
        if not pkt or len(pkt.payload) < 1:
            return None
        return pkt.payload[0] == 1

    def set_night_vision(self, enabled: bool) -> Optional[bool]:
        pkt = self.send(0x63, struct.pack("<B", 1 if enabled else 0))
        if not pkt or len(pkt.payload) < 1:
            return None
        return pkt.payload[0] == 1

    def soft_restart(self, camera: bool = False, gimbal: bool = False) -> Optional[Dict[str, bool]]:
        pkt = self.send(0x80, struct.pack("<BB", 1 if camera else 0, 1 if gimbal else 0))
        if not pkt or len(pkt.payload) < 2:
            return None
        return {"camera_restart": pkt.payload[0] == 1, "gimbal_restart": pkt.payload[1] == 1}

    @staticmethod
    def _ack_success(pkt: Optional[MT11Packet]) -> Optional[bool]:
        if pkt is None:
            return None
        if len(pkt.payload) < 1:
            return True
        return pkt.payload[0] == 1

    @staticmethod
    def hexdump(packet: Optional[MT11Packet | bytes]) -> str:
        if isinstance(packet, MT11Packet):
            return hexdump(packet.raw)
        return hexdump(packet)
