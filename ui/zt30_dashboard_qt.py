#!/usr/bin/env python3
"""Clean PyQt5 dashboard for SIYI ZT30 control and RTSP monitoring."""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtCore import QThread, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QFont, QImage, QKeySequence, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from siyi_zt30 import DEFAULT_IP, DEFAULT_PORT, ZT30UDPClient
from siyi_zt30.constants import IMAGE_MODE_BY_NAME, IMAGE_MODES, THERMAL_PALETTES


STREAM_SIZE = (960, 540)
PIP_SIZE = (320, 180)

CAMERA_VIEWS = {
    "Zoom + Thermal": "single_zoom_sub_thermal",
    "Zoom + Wide": "single_zoom_sub_wide",
    "Wide + Thermal": "single_wide_sub_thermal",
    "Wide + Zoom": "single_wide_sub_zoom",
    "Thermal + Zoom": "single_thermal_sub_zoom",
    "Thermal + Wide": "single_thermal_sub_wide",
    "Split Zoom/Thermal": "split_zoom_thermal_sub_wide",
    "Split Wide/Thermal": "split_wide_thermal_sub_zoom",
    "Split Zoom/Wide": "split_zoom_wide_sub_thermal",
}

THERMAL_NAMES = {
    "White Hot": "white_hot",
    "Black Hot": "black_hot",
    "Ironbow": "ironbow",
    "Rainbow": "rainbow",
    "Red Hot": "red_hot",
    "Night": "night",
    "Sepia": "sepia",
    "Aurora": "aurora",
}


def rtsp_url(host: str, stream: int) -> str:
    return f"rtsp://{host}:8554/video{stream}"


def clamp(value: int, min_value: int, max_value: int) -> int:
    return max(min_value, min(max_value, int(value)))


class FFmpegStreamThread(QThread):
    frame_ready = pyqtSignal(QImage)
    status_changed = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, url: str, width: int, height: int, parent=None):
        super().__init__(parent)
        self.url = url
        self.width = width
        self.height = height
        self._running = False
        self._process: Optional[subprocess.Popen] = None

    def run(self):
        if shutil.which("ffmpeg") is None:
            self.error.emit("ffmpeg not found")
            return

        frame_bytes = self.width * self.height * 3
        vf = (
            f"fps=20,scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,"
            f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2"
        )
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-rtsp_transport",
            "tcp",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-i",
            self.url,
            "-an",
            "-vf",
            vf,
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ]

        self._running = True
        self.status_changed.emit("Connecting")
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=frame_bytes * 2,
            )
            self.status_changed.emit("Live")
            while self._running and self._process.stdout is not None:
                frame = self._read_exact(frame_bytes)
                if frame is None:
                    break
                image = QImage(frame, self.width, self.height, self.width * 3, QImage.Format_RGB888).copy()
                self.frame_ready.emit(image)
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self._stop_process()
            if self._running:
                self.status_changed.emit("Stopped")

    def _read_exact(self, size: int):
        if self._process is None or self._process.stdout is None:
            return None
        data = bytearray()
        while self._running and len(data) < size:
            chunk = self._process.stdout.read(size - len(data))
            if not chunk:
                return None
            data.extend(chunk)
        return bytes(data)

    def stop(self):
        self._running = False
        self._stop_process()
        self.wait(1200)

    def _stop_process(self):
        if self._process is None:
            return
        try:
            self._process.terminate()
            self._process.wait(timeout=1.0)
        except Exception:
            try:
                self._process.kill()
            except Exception:
                pass
        self._process = None


class JoystickThread(QThread):
    speed_changed = pyqtSignal(int, int)
    status_changed = pyqtSignal(str)
    message = pyqtSignal(str)

    def __init__(self, device: str, speed_getter: Callable[[], int], deadzone_getter: Callable[[], int], parent=None):
        super().__init__(parent)
        self.device = device
        self.speed_getter = speed_getter
        self.deadzone_getter = deadzone_getter
        self._running = False
        self._axes = {4: 0, 5: 0}

    def run(self):
        event_size = struct.calcsize("IhBB")
        fd = None
        self._running = True
        try:
            fd = os.open(self.device, os.O_RDONLY | os.O_NONBLOCK)
            self.status_changed.emit("Active")
            self.message.emit(f"Joystick active: {self.device}")
            next_send = 0.0
            while self._running:
                try:
                    data = os.read(fd, event_size)
                except BlockingIOError:
                    data = b""

                if len(data) == event_size:
                    _time_ms, value, event_type, axis = struct.unpack("IhBB", data)
                    if event_type & 0x02 and axis in (4, 5):
                        self._axes[axis] = value

                now = time.monotonic()
                if now >= next_send:
                    self.speed_changed.emit(self._axis_to_speed(self._axes[4]), self._axis_to_speed(self._axes[5]))
                    next_send = now + 0.10
                time.sleep(0.01)
        except FileNotFoundError:
            self.status_changed.emit("Missing")
            self.message.emit(f"Joystick not found: {self.device}")
        except PermissionError:
            self.status_changed.emit("No access")
            self.message.emit(f"Joystick permission denied: {self.device}")
        except OSError as exc:
            self.status_changed.emit("Error")
            self.message.emit(f"Joystick error: {exc}")
        finally:
            if fd is not None:
                os.close(fd)
            self.speed_changed.emit(0, 0)

    def stop(self):
        self._running = False
        self.wait(800)

    def _axis_to_speed(self, value: int) -> int:
        speed_limit = clamp(self.speed_getter(), 0, 100)
        deadzone = clamp(self.deadzone_getter(), 0, 32000)
        value = clamp(value, -32767, 32767)
        magnitude = abs(value)
        if magnitude <= deadzone:
            return 0
        scaled = round(((magnitude - deadzone) / max(1, 32767 - deadzone)) * speed_limit)
        return clamp(scaled, 0, speed_limit) * (1 if value > 0 else -1)


class VideoStage(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("videoStage")
        self.setMinimumSize(780, 440)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.main_label = QLabel("Select Play to start the stream")
        self.main_label.setObjectName("mainVideo")
        self.main_label.setAlignment(Qt.AlignCenter)
        self.main_label.setScaledContents(False)

        self.pip_label = QLabel("PiP")
        self.pip_label.setObjectName("pipVideo")
        self.pip_label.setAlignment(Qt.AlignCenter)
        self.pip_label.setFixedSize(*PIP_SIZE)
        self.pip_label.hide()

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.main_label, 0, 0)
        layout.addWidget(self.pip_label, 0, 0, Qt.AlignRight | Qt.AlignBottom)

        self._main_pixmap: Optional[QPixmap] = None
        self._pip_pixmap: Optional[QPixmap] = None

    def set_main_frame(self, image: QImage):
        self._main_pixmap = QPixmap.fromImage(image)
        self._refresh_pixmaps()

    def set_pip_frame(self, image: QImage):
        self._pip_pixmap = QPixmap.fromImage(image)
        self.pip_label.show()
        self._refresh_pixmaps()

    def clear_main(self, text: str):
        self._main_pixmap = None
        self.main_label.setPixmap(QPixmap())
        self.main_label.setText(text)

    def set_pip_enabled(self, enabled: bool):
        if enabled:
            self.pip_label.show()
        else:
            self.pip_label.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_pixmaps()

    def _refresh_pixmaps(self):
        if self._main_pixmap:
            self.main_label.setPixmap(
                self._main_pixmap.scaled(self.main_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        if self._pip_pixmap:
            self.pip_label.setPixmap(
                self._pip_pixmap.scaled(self.pip_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )


class ZT30QtDashboard(QMainWindow):
    log_signal = pyqtSignal(str)
    telemetry_signal = pyqtSignal(object, object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ZT30 Dashboard")
        self.resize(1440, 900)
        self.client: Optional[ZT30UDPClient] = None
        self.main_thread: Optional[FFmpegStreamThread] = None
        self.pip_thread: Optional[FFmpegStreamThread] = None
        self.joystick_thread: Optional[JoystickThread] = None
        self.last_joystick_speed = (None, None)

        self._build_ui()
        self._apply_style()
        self.log_signal.connect(self.log.append)
        self.telemetry_signal.connect(self._apply_telemetry)
        self._connect_client()

        self.telemetry_timer = QTimer(self)
        self.telemetry_timer.timeout.connect(self.refresh_status)
        self.telemetry_timer.start(1500)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        shell = QVBoxLayout(root)
        shell.setContentsMargins(20, 18, 20, 20)
        shell.setSpacing(14)

        shell.addLayout(self._build_header())

        content = QHBoxLayout()
        content.setSpacing(14)
        content.addLayout(self._build_video_column(), 1)
        content.addWidget(self._build_control_panel(), 0)
        shell.addLayout(content, 1)

    def _build_header(self):
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("ZT30 Dashboard")
        title.setObjectName("title")
        subtitle = QLabel("Clean control surface focused on the live camera feed")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box, 1)

        self.host_edit = QLineEdit(DEFAULT_IP)
        self.host_edit.setFixedWidth(150)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(DEFAULT_PORT)
        reconnect = QPushButton("Connect")
        reconnect.clicked.connect(self._connect_client)
        header.addWidget(QLabel("Camera IP"))
        header.addWidget(self.host_edit)
        header.addWidget(QLabel("UDP Port"))
        header.addWidget(self.port_spin)
        header.addWidget(reconnect)
        return header

    def _build_video_column(self):
        column = QVBoxLayout()
        column.setSpacing(12)

        toolbar = QFrame()
        toolbar.setObjectName("toolbar")
        tools = QHBoxLayout(toolbar)
        tools.setContentsMargins(12, 10, 12, 10)
        tools.setSpacing(10)

        self.stream_group = QButtonGroup(self)
        self.video1_btn = self._chip("Video 1", True)
        self.video2_btn = self._chip("Video 2", False)
        self.stream_group.addButton(self.video1_btn, 1)
        self.stream_group.addButton(self.video2_btn, 2)
        tools.addWidget(QLabel("Live view"))
        tools.addWidget(self.video1_btn)
        tools.addWidget(self.video2_btn)

        self.pip_check = QCheckBox("Picture in Picture")
        tools.addWidget(self.pip_check)
        self.status_badge = QLabel("Idle")
        self.status_badge.setObjectName("statusBadge")
        tools.addWidget(self.status_badge)
        tools.addStretch(1)

        play = QPushButton("Play")
        play.clicked.connect(self.start_streams)
        stop = QPushButton("Stop")
        stop.clicked.connect(self.stop_streams)
        tools.addWidget(play)
        tools.addWidget(stop)

        self.video_stage = VideoStage()
        column.addWidget(toolbar)
        column.addWidget(self.video_stage, 1)
        return column

    def _build_control_panel(self):
        scroll = QScrollArea()
        scroll.setObjectName("controlScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setFixedWidth(400)

        panel = QFrame()
        panel.setObjectName("sidePanel")
        panel.setFixedWidth(380)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        layout.addWidget(self._build_quick_actions())
        layout.addWidget(self._build_gimbal_controls())
        layout.addWidget(self._build_camera_controls())
        layout.addWidget(self._build_laser_controls())
        layout.addWidget(self._build_joystick_controls())
        layout.addWidget(self._build_status_cards())

        self.log = QTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        self.log.setFixedHeight(120)
        layout.addWidget(self.log)
        layout.addStretch(1)
        scroll.setWidget(panel)
        return scroll

    def _build_quick_actions(self):
        box = self._section("Quick Actions")
        row = QHBoxLayout()
        photo = QPushButton("Photo")
        photo.clicked.connect(lambda: self.run_command("photo", self.client.take_photo))
        record = QPushButton("Record")
        record.clicked.connect(lambda: self.run_command("record", self.client.toggle_record))
        focus = QPushButton("Auto Focus")
        focus.clicked.connect(lambda: self.run_command("auto focus", self.client.auto_focus))
        row.addWidget(photo)
        row.addWidget(record)
        row.addWidget(focus)
        box.layout().addLayout(row)
        return box

    def _build_gimbal_controls(self):
        box = self._section("Gimbal")
        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(5, 100)
        self.speed_slider.setValue(35)
        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel("Speed"))
        speed_row.addWidget(self.speed_slider, 1)
        self.speed_label = QLabel("35")
        self.speed_slider.valueChanged.connect(lambda value: self.speed_label.setText(str(value)))
        speed_row.addWidget(self.speed_label)
        box.layout().addLayout(speed_row)

        pad = QGridLayout()
        pad.addWidget(self._hold_button("Up", 0, 1), 0, 1)
        pad.addWidget(self._hold_button("Left", -1, 0), 1, 0)
        center = QPushButton("Center")
        center.clicked.connect(lambda: self.run_command("center", self.client.center))
        pad.addWidget(center, 1, 1)
        pad.addWidget(self._hold_button("Right", 1, 0), 1, 2)
        pad.addWidget(self._hold_button("Down", 0, -1), 2, 1)
        box.layout().addLayout(pad)

        modes = QHBoxLayout()
        for label, mode in (("Lock", "lock"), ("Follow", "follow"), ("FPV", "fpv")):
            btn = QPushButton(label)
            btn.clicked.connect(lambda _checked=False, value=mode: self.run_command(f"{value} mode", lambda: self.client.set_motion_mode(value)))
            modes.addWidget(btn)
        box.layout().addLayout(modes)

        angle_row = QHBoxLayout()
        self.yaw_spin = QDoubleSpinBox()
        self.yaw_spin.setRange(-180.0, 180.0)
        self.yaw_spin.setSuffix(" deg")
        self.pitch_spin = QDoubleSpinBox()
        self.pitch_spin.setRange(-90.0, 90.0)
        self.pitch_spin.setSuffix(" deg")
        set_angle = QPushButton("Set Angle")
        set_angle.clicked.connect(lambda: self.run_command("set angle", lambda: self.client.set_angle(self.yaw_spin.value(), self.pitch_spin.value())))
        angle_row.addWidget(QLabel("Pan"))
        angle_row.addWidget(self.yaw_spin)
        angle_row.addWidget(QLabel("Tilt"))
        angle_row.addWidget(self.pitch_spin)
        angle_row.addWidget(set_angle)
        box.layout().addLayout(angle_row)
        return box

    def _build_camera_controls(self):
        box = self._section("Camera")

        zoom_row = QHBoxLayout()
        zoom_out = self._press_button("Zoom Out", self.client_zoom_out, self.client_zoom_stop)
        zoom_in = self._press_button("Zoom In", self.client_zoom_in, self.client_zoom_stop)
        self.zoom_spin = QDoubleSpinBox()
        self.zoom_spin.setRange(1.0, 30.9)
        self.zoom_spin.setSingleStep(0.5)
        self.zoom_spin.setValue(4.5)
        set_zoom = QPushButton("Set")
        set_zoom.clicked.connect(lambda: self.run_command("set zoom", lambda: self.client.absolute_zoom(self.zoom_spin.value())))
        zoom_row.addWidget(zoom_out)
        zoom_row.addWidget(zoom_in)
        zoom_row.addWidget(self.zoom_spin)
        zoom_row.addWidget(set_zoom)
        box.layout().addLayout(zoom_row)

        focus_row = QHBoxLayout()
        focus_row.addWidget(self._press_button("Focus Near", self.client_focus_near, self.client_focus_stop))
        focus_row.addWidget(self._press_button("Focus Far", self.client_focus_far, self.client_focus_stop))
        box.layout().addLayout(focus_row)

        self.view_combo = QComboBox()
        self.view_combo.addItems(CAMERA_VIEWS.keys())
        apply_view = QPushButton("Apply View")
        apply_view.clicked.connect(self.apply_camera_view)
        view_row = QHBoxLayout()
        view_row.addWidget(QLabel("View"))
        view_row.addWidget(self.view_combo, 1)
        view_row.addWidget(apply_view)
        box.layout().addLayout(view_row)

        self.palette_combo = QComboBox()
        self.palette_combo.addItems(THERMAL_NAMES.keys())
        palette_btn = QPushButton("Set Palette")
        palette_btn.clicked.connect(self.apply_palette)
        thermal_row = QHBoxLayout()
        thermal_row.addWidget(QLabel("Thermal"))
        thermal_row.addWidget(self.palette_combo, 1)
        thermal_row.addWidget(palette_btn)
        box.layout().addLayout(thermal_row)

        temp_row = QHBoxLayout()
        self.temp_x_spin = QSpinBox()
        self.temp_x_spin.setRange(0, 1920)
        self.temp_x_spin.setValue(320)
        self.temp_y_spin = QSpinBox()
        self.temp_y_spin.setRange(0, 1080)
        self.temp_y_spin.setValue(256)
        point_temp = QPushButton("Point Temp")
        point_temp.clicked.connect(lambda: self.run_command("point temperature", lambda: self.client.request_temperature_point(self.temp_x_spin.value(), self.temp_y_spin.value())))
        full_temp = QPushButton("Full Temp")
        full_temp.clicked.connect(lambda: self.run_command("full temperature", self.client.request_temperature_full_image))
        temp_row.addWidget(QLabel("X"))
        temp_row.addWidget(self.temp_x_spin)
        temp_row.addWidget(QLabel("Y"))
        temp_row.addWidget(self.temp_y_spin)
        temp_row.addWidget(point_temp)
        temp_row.addWidget(full_temp)
        box.layout().addLayout(temp_row)
        return box

    def _build_laser_controls(self):
        box = self._section("Laser Rangefinder")
        row = QHBoxLayout()
        laser_on = QPushButton("Laser On")
        laser_on.clicked.connect(lambda: self.run_command("laser on", lambda: self.client.set_laser(True)))
        laser_off = QPushButton("Laser Off")
        laser_off.clicked.connect(lambda: self.run_command("laser off", lambda: self.client.set_laser(False)))
        measure = QPushButton("Measure")
        measure.clicked.connect(lambda: self.run_command("laser range", self.client.request_laser_range))
        target = QPushButton("Target GPS")
        target.clicked.connect(lambda: self.run_command("laser target", self.client.request_laser_target_latlon))
        row.addWidget(laser_on)
        row.addWidget(laser_off)
        row.addWidget(measure)
        row.addWidget(target)
        box.layout().addLayout(row)
        return box

    def _build_joystick_controls(self):
        box = self._section("Joystick")
        self.joystick_check = QCheckBox("Use /dev/input/js0")
        self.joystick_check.toggled.connect(self.toggle_joystick)
        self.joystick_status = QLabel("Off")
        top = QHBoxLayout()
        top.addWidget(self.joystick_check)
        top.addStretch(1)
        top.addWidget(self.joystick_status)
        box.layout().addLayout(top)

        self.deadzone_slider = QSlider(Qt.Horizontal)
        self.deadzone_slider.setRange(0, 16000)
        self.deadzone_slider.setValue(5000)
        dz = QHBoxLayout()
        dz.addWidget(QLabel("Deadzone"))
        dz.addWidget(self.deadzone_slider, 1)
        box.layout().addLayout(dz)
        return box

    def _build_status_cards(self):
        box = self._section("Status")
        grid = QGridLayout()
        self.yaw_value = self._metric("Yaw")
        self.pitch_value = self._metric("Pitch")
        self.roll_value = self._metric("Roll")
        self.zoom_value = self._metric("Zoom")
        grid.addWidget(self.yaw_value[0], 0, 0)
        grid.addWidget(self.yaw_value[1], 1, 0)
        grid.addWidget(self.pitch_value[0], 0, 1)
        grid.addWidget(self.pitch_value[1], 1, 1)
        grid.addWidget(self.roll_value[0], 2, 0)
        grid.addWidget(self.roll_value[1], 3, 0)
        grid.addWidget(self.zoom_value[0], 2, 1)
        grid.addWidget(self.zoom_value[1], 3, 1)
        box.layout().addLayout(grid)
        refresh = QPushButton("Refresh Status")
        refresh.clicked.connect(self.refresh_status)
        box.layout().addWidget(refresh)
        return box

    def _section(self, title: str):
        box = QGroupBox(title)
        layout = QVBoxLayout(box)
        layout.setSpacing(10)
        return box

    def _metric(self, name: str):
        label = QLabel(name)
        label.setObjectName("metricLabel")
        value = QLabel("-")
        value.setObjectName("metricValue")
        return label, value

    def _chip(self, label: str, checked: bool):
        btn = QToolButton()
        btn.setText(label)
        btn.setCheckable(True)
        btn.setChecked(checked)
        btn.setObjectName("chip")
        return btn

    def _hold_button(self, label: str, yaw_dir: int, pitch_dir: int):
        btn = QPushButton(label)
        btn.pressed.connect(lambda: self.rotate(yaw_dir * self.speed_slider.value(), pitch_dir * self.speed_slider.value()))
        btn.released.connect(self.stop_rotation)
        return btn

    def _press_button(self, label: str, start: Callable[[], None], stop: Callable[[], None]):
        btn = QPushButton(label)
        btn.pressed.connect(start)
        btn.released.connect(stop)
        return btn

    def _connect_client(self):
        self.stop_joystick()
        if self.client:
            self.client.close()
        self.client = ZT30UDPClient(self.host_edit.text().strip(), self.port_spin.value())
        self.log_message(f"Connected to {self.client.host}:{self.client.port}")

    def start_streams(self):
        self.stop_streams()
        stream = self.stream_group.checkedId() or 1
        other = 2 if stream == 1 else 1
        host = self.host_edit.text().strip()
        self.main_thread = self._start_stream(rtsp_url(host, stream), *STREAM_SIZE, self.video_stage.set_main_frame)
        if self.pip_check.isChecked():
            self.video_stage.set_pip_enabled(True)
            self.pip_thread = self._start_stream(rtsp_url(host, other), *PIP_SIZE, self.video_stage.set_pip_frame)
        else:
            self.video_stage.set_pip_enabled(False)

    def _start_stream(self, url: str, width: int, height: int, frame_slot):
        thread = FFmpegStreamThread(url, width, height, self)
        thread.frame_ready.connect(frame_slot)
        thread.status_changed.connect(self.status_badge.setText)
        thread.error.connect(lambda text: self.log_message(f"stream error: {text}"))
        thread.start()
        self.log_message(f"Playing {url}")
        return thread

    def stop_streams(self):
        if self.main_thread:
            self.main_thread.stop()
            self.main_thread = None
        if self.pip_thread:
            self.pip_thread.stop()
            self.pip_thread = None
        self.status_badge.setText("Idle")
        self.video_stage.clear_main("Select Play to start the stream")

    def run_command(self, label: str, command: Callable):
        if not self.client:
            self.log_message(f"{label}: not connected")
            return
        threading.Thread(target=self._command_worker, args=(label, command), daemon=True).start()

    def _command_worker(self, label: str, command: Callable):
        try:
            result = command()
            self.log_message(f"{label}: {result if result is not None else 'OK'}")
        except Exception as exc:
            self.log_message(f"{label}: ERROR {exc}")

    def rotate(self, yaw: int, pitch: int):
        self.run_command("move", lambda: self.client.rotate_speed(yaw, pitch))

    def stop_rotation(self):
        self.run_command("stop", self.client.stop_rotation)

    def client_zoom_in(self):
        self.run_command("zoom in", self.client.zoom_in)

    def client_zoom_out(self):
        self.run_command("zoom out", self.client.zoom_out)

    def client_zoom_stop(self):
        self.run_command("zoom stop", self.client.zoom_stop)

    def client_focus_near(self):
        self.run_command("focus near", self.client.focus_near)

    def client_focus_far(self):
        self.run_command("focus far", self.client.focus_far)

    def client_focus_stop(self):
        self.run_command("focus stop", self.client.focus_stop)

    def apply_camera_view(self):
        mode = CAMERA_VIEWS[self.view_combo.currentText()]
        if mode not in IMAGE_MODE_BY_NAME:
            QMessageBox.warning(self, "View not available", "This camera view is not available in the SDK mapping.")
            return
        self.run_command("camera view", lambda: self.client.set_image_mode(mode))

    def apply_palette(self):
        palette = THERMAL_NAMES[self.palette_combo.currentText()]
        if palette not in THERMAL_PALETTES.values():
            QMessageBox.warning(self, "Palette not available", "This thermal palette is not available in the SDK mapping.")
            return
        self.run_command("thermal palette", lambda: self.client.set_thermal_palette(palette))

    def refresh_status(self):
        if not self.client:
            return
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self):
        try:
            attitude = self.client.request_attitude()
            zoom = self.client.request_zoom()
            self.telemetry_signal.emit(attitude, zoom)
        except Exception as exc:
            self.log_message(f"status: ERROR {exc}")

    def _apply_telemetry(self, attitude, zoom):
        if attitude:
            self.yaw_value[1].setText(f"{attitude['yaw_deg']:.1f}")
            self.pitch_value[1].setText(f"{attitude['pitch_deg']:.1f}")
            self.roll_value[1].setText(f"{attitude['roll_deg']:.1f}")
        if zoom is not None:
            self.zoom_value[1].setText(f"{zoom:.1f}x")

    def toggle_joystick(self, enabled: bool):
        if enabled:
            self.start_joystick()
        else:
            self.stop_joystick()

    def start_joystick(self):
        self.stop_joystick()
        self.joystick_thread = JoystickThread(
            "/dev/input/js0",
            self.speed_slider.value,
            self.deadzone_slider.value,
            self,
        )
        self.joystick_thread.speed_changed.connect(self.handle_joystick_speed)
        self.joystick_thread.status_changed.connect(self.joystick_status.setText)
        self.joystick_thread.message.connect(self.log_message)
        self.joystick_thread.start()

    def stop_joystick(self):
        if self.joystick_thread:
            self.joystick_thread.stop()
            self.joystick_thread = None
        self.joystick_status.setText("Off")
        if self.client:
            try:
                self.client.stop_rotation()
            except Exception:
                pass

    def handle_joystick_speed(self, yaw: int, pitch: int):
        speed = (yaw, pitch)
        if speed == self.last_joystick_speed or not self.client:
            return
        self.last_joystick_speed = speed
        threading.Thread(target=lambda: self.client.rotate_speed(yaw, pitch), daemon=True).start()

    def log_message(self, text: str):
        self.log_signal.emit(text)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Refresh):
            self.refresh_status()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self.stop_joystick()
        self.stop_streams()
        if self.client:
            self.client.close()
        event.accept()

    def _apply_style(self):
        QApplication.instance().setStyleSheet(
            """
            QWidget {
                background: #f5f7fb;
                color: #18212f;
                font-family: Inter, Segoe UI, Arial;
                font-size: 13px;
            }
            #title {
                font-size: 26px;
                font-weight: 700;
                color: #111827;
            }
            #subtitle {
                color: #6b7280;
            }
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
                background: #ffffff;
                border: 1px solid #d8dee8;
                border-radius: 6px;
                padding: 7px 9px;
                min-height: 20px;
            }
            QPushButton, QToolButton {
                background: #ffffff;
                border: 1px solid #d8dee8;
                border-radius: 7px;
                padding: 8px 12px;
                font-weight: 600;
            }
            QPushButton:hover, QToolButton:hover {
                background: #eef4ff;
                border-color: #9bb8e8;
            }
            QPushButton:pressed, QToolButton:pressed {
                background: #dfeaff;
            }
            #chip:checked {
                background: #1769e0;
                border-color: #1769e0;
                color: #ffffff;
            }
            #toolbar, #sidePanel, QGroupBox {
                background: #ffffff;
                border: 1px solid #e1e7f0;
                border-radius: 10px;
            }
            #controlScroll {
                background: transparent;
                border: 0;
            }
            #toolbar {
                max-height: 58px;
            }
            QGroupBox {
                margin-top: 12px;
                padding: 14px;
                font-weight: 700;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 5px;
                color: #374151;
            }
            #videoStage {
                background: #0a0f17;
                border-radius: 12px;
                border: 1px solid #111827;
            }
            #mainVideo {
                background: #05070b;
                color: #7b8794;
                border-radius: 12px;
                font-size: 18px;
            }
            #pipVideo {
                background: #05070b;
                color: #a7b1bf;
                border: 2px solid #ffffff;
                border-radius: 8px;
                margin: 18px;
            }
            #statusBadge {
                background: #e8f7f2;
                color: #087456;
                border-radius: 11px;
                padding: 5px 10px;
                font-weight: 700;
            }
            #metricLabel {
                color: #6b7280;
                font-size: 12px;
            }
            #metricValue {
                color: #111827;
                font-size: 20px;
                font-weight: 700;
            }
            #log {
                background: #f8fafc;
                border: 1px solid #e1e7f0;
                border-radius: 8px;
                color: #374151;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #e5eaf2;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #1769e0;
                width: 16px;
                margin: -5px 0;
                border-radius: 8px;
            }
            """
        )


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ZT30 Dashboard")
    app.setFont(QFont("Inter", 10))
    window = ZT30QtDashboard()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
