#!/usr/bin/env python3
"""Clean PyQt5 dashboard for SIYI ZT30 control and RTSP monitoring."""

from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse
from urllib.request import urlopen

from PyQt5.QtCore import QLibraryInfo, QPoint, QPointF, QRect, QSize, QThread, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QImage, QKeySequence, QLinearGradient, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = QLibraryInfo.location(QLibraryInfo.PluginsPath)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from siyi_zt30 import AITrackingBox, DEFAULT_AI_IP, DEFAULT_IP, DEFAULT_PORT, SiyiAITrackingClient, ZT30UDPClient, ZT30WebClient
from siyi_zt30.constants import IMAGE_MODE_BY_NAME, IMAGE_MODES, THERMAL_PALETTES


STREAM_SIZE = (960, 540)
PIP_SIZE_DEFAULT = (320, 180)
AI_COORD_SIZE = (1280, 720)

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


def ai_rtsp_url(host: str) -> str:
    return f"rtsp://{host}:554/video0"


def clamp(value: int, min_value: int, max_value: int) -> int:
    return max(min_value, min(max_value, int(value)))


class CockpitBackground(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(55)

    def _tick(self):
        self._phase = (self._phase + 1) % 720
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        painter.fillRect(rect, QColor("#000000"))

        wash = QLinearGradient(QPointF(0, rect.height()), QPointF(rect.width(), 0))
        wash.setColorAt(0.0, QColor(0, 176, 190, 88))
        wash.setColorAt(0.26, QColor(0, 54, 65, 66))
        wash.setColorAt(0.58, QColor(2, 12, 15, 118))
        wash.setColorAt(1.0, QColor(0, 0, 0, 255))
        painter.fillRect(rect, wash)

        sweep = QLinearGradient(QPointF(rect.width() * 0.2, 0), QPointF(rect.width(), rect.height()))
        sweep.setColorAt(0.0, QColor(0, 238, 255, 28))
        sweep.setColorAt(0.45, QColor(0, 120, 140, 18))
        sweep.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(rect, sweep)

        horizon = QPen(QColor(255, 255, 255, 18), 1)
        painter.setPen(horizon)
        y = int(rect.height() * (0.52 + 0.015 * math.sin(self._phase / 40.0)))
        painter.drawLine(0, y, rect.width(), y)
        painter.end()


class FFmpegStreamThread(QThread):
    frame_ready = pyqtSignal(QImage)
    status_changed = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, url: str, width: int, height: int, transport: str = "tcp", parent=None):
        super().__init__(parent)
        self.url = url
        self.width = width
        self.height = height
        self.transport = transport
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
            self.transport,
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

                image = QImage(
                    frame,
                    self.width,
                    self.height,
                    self.width * 3,
                    QImage.Format_RGB888,
                ).copy()

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

    def __init__(
        self,
        device: str,
        speed_getter: Callable[[], int],
        deadzone_getter: Callable[[], int],
        parent=None,
    ):
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
                    self.speed_changed.emit(
                        self._axis_to_speed(self._axes[4]),
                        self._axis_to_speed(self._axes[5]),
                    )
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


class ClickableVideoLabel(QLabel):
    clicked = pyqtSignal()
    clicked_at = pyqtSignal(QPoint)
    pressed_at = pyqtSignal(QPoint)
    moved_at = pyqtSignal(QPoint)
    released_at = pyqtSignal(QPoint)
    right_clicked = pyqtSignal()

    def __init__(self, text: str = ""):
        super().__init__(text)
        self._last_pixmap: Optional[QPixmap] = None

    def setPixmap(self, pixmap: QPixmap):
        self._last_pixmap = pixmap
        super().setPixmap(pixmap)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.pressed_at.emit(event.pos())
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self.moved_at.emit(event.pos())
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            self.clicked_at.emit(event.pos())
            self.released_at.emit(event.pos())
            return

        if event.button() == Qt.RightButton:
            self.right_clicked.emit()
            return

        super().mouseReleaseEvent(event)


class VideoStage(QFrame):
    pip_clicked = pyqtSignal()
    main_clicked = pyqtSignal(int, int)
    main_box_selected = pyqtSignal(int, int, int, int)
    cancel_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("videoStage")
        self.setMinimumSize(780, 440)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self.main_label = ClickableVideoLabel("Select Play to start the stream")
        self.main_label.setObjectName("mainVideo")
        self.main_label.setAlignment(Qt.AlignCenter)
        self.main_label.setScaledContents(False)
        self.main_label.pressed_at.connect(self._handle_main_press)
        self.main_label.moved_at.connect(self._handle_main_move)
        self.main_label.released_at.connect(self._handle_main_release)
        self.main_label.right_clicked.connect(self._handle_cancel_requested)

        self.pip_label = ClickableVideoLabel("PiP")
        self.pip_label.setObjectName("pipVideo")
        self.pip_label.setAlignment(Qt.AlignCenter)
        self.pip_label.setFixedSize(*PIP_SIZE_DEFAULT)
        self.pip_label.clicked.connect(self.pip_clicked.emit)
        self.pip_label.hide()

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.main_label, 0, 0)
        layout.addWidget(self.pip_label, 0, 0, Qt.AlignRight | Qt.AlignBottom)

        self._main_pixmap: Optional[QPixmap] = None
        self._pip_pixmap: Optional[QPixmap] = None
        self._laser_overlay_lines = []
        self._ai_tracking_box: Optional[AITrackingBox] = None
        self._selection_start: Optional[tuple[int, int]] = None
        self._selection_end: Optional[tuple[int, int]] = None

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

    def set_pip_size(self, width: int):
        width = clamp(width, 160, 640)
        height = round(width * 9 / 16)
        self.pip_label.setFixedSize(QSize(width, height))
        self._refresh_pixmaps()

    def set_laser_overlay(self, lines):
        self._laser_overlay_lines = [line for line in lines if line]
        self._refresh_pixmaps()

    def clear_laser_overlay(self):
        self._laser_overlay_lines = []
        self._refresh_pixmaps()

    def set_ai_tracking_box(self, box: Optional[AITrackingBox]):
        self._ai_tracking_box = box
        self._refresh_pixmaps()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_pixmaps()

    def _refresh_pixmaps(self):
        if self._main_pixmap:
            pixmap = self._main_pixmap.scaled(
                self.main_label.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self._draw_main_overlay(pixmap)
            self.main_label.setPixmap(pixmap)

        if self._pip_pixmap:
            self.pip_label.setPixmap(
                self._pip_pixmap.scaled(
                    self.pip_label.size(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )

    def _draw_main_overlay(self, pixmap: QPixmap):
        if pixmap.isNull():
            return

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)

        cx = pixmap.width() // 2
        cy = pixmap.height() // 2
        arm = max(24, min(pixmap.width(), pixmap.height()) // 14)
        gap = 8

        shadow = QPen(QColor(0, 0, 0, 190), 4)
        painter.setPen(shadow)
        painter.drawLine(cx - arm, cy, cx - gap, cy)
        painter.drawLine(cx + gap, cy, cx + arm, cy)
        painter.drawLine(cx, cy - arm, cx, cy - gap)
        painter.drawLine(cx, cy + gap, cx, cy + arm)
        painter.drawEllipse(QPoint(cx, cy), 3, 3)

        pen = QPen(QColor("#f7d84a"), 2)
        painter.setPen(pen)
        painter.drawLine(cx - arm, cy, cx - gap, cy)
        painter.drawLine(cx + gap, cy, cx + arm, cy)
        painter.drawLine(cx, cy - arm, cx, cy - gap)
        painter.drawLine(cx, cy + gap, cx, cy + arm)
        painter.drawEllipse(QPoint(cx, cy), 3, 3)

        if self._laser_overlay_lines:
            painter.setFont(QFont("Inter", 10, QFont.DemiBold))
            metrics = painter.fontMetrics()
            line_height = metrics.height()
            width = max(metrics.horizontalAdvance(line) for line in self._laser_overlay_lines) + 22
            height = line_height * len(self._laser_overlay_lines) + 18
            rect = QRect(14, 14, width, height)
            painter.fillRect(rect, QColor(3, 5, 7, 185))
            painter.setPen(QPen(QColor("#2db69c"), 1))
            painter.drawRect(rect)
            painter.setPen(QPen(QColor("#eef3f7"), 1))
            y = rect.y() + 12 + metrics.ascent()
            for line in self._laser_overlay_lines:
                painter.drawText(rect.x() + 11, y, line)
                y += line_height

        if self._selection_start and self._selection_end:
            self._draw_selection_box(painter, pixmap)

        painter.end()

    def _draw_ai_tracking_box(self, painter: QPainter, pixmap: QPixmap, box: AITrackingBox):
        scale_x = pixmap.width() / AI_COORD_SIZE[0]
        scale_y = pixmap.height() / AI_COORD_SIZE[1]

        left = clamp(round(box.left * scale_x), 0, pixmap.width() - 1)
        top = clamp(round(box.top * scale_y), 0, pixmap.height() - 1)
        right = clamp(round(box.right * scale_x), 0, pixmap.width() - 1)
        bottom = clamp(round(box.bottom * scale_y), 0, pixmap.height() - 1)

        rect = QRect(left, top, max(2, right - left), max(2, bottom - top))
        painter.setPen(QPen(QColor(0, 0, 0, 210), 5))
        painter.drawRect(rect)
        color = QColor("#2fd16d") if box.track_state in (0, 4) else QColor("#f7d84a")
        painter.setPen(QPen(color, 3))
        painter.drawRect(rect)

    def _draw_selection_box(self, painter: QPainter, pixmap: QPixmap):
        left, top, right, bottom = self._normalized_selection()
        scale_x = pixmap.width() / AI_COORD_SIZE[0]
        scale_y = pixmap.height() / AI_COORD_SIZE[1]
        rect = QRect(
            clamp(round(left * scale_x), 0, pixmap.width() - 1),
            clamp(round(top * scale_y), 0, pixmap.height() - 1),
            max(2, round((right - left) * scale_x)),
            max(2, round((bottom - top) * scale_y)),
        )
        painter.setPen(QPen(QColor(0, 0, 0, 220), 5))
        painter.drawRect(rect)
        painter.setPen(QPen(QColor("#42a5ff"), 2))
        painter.drawRect(rect)

    def _normalized_selection(self):
        sx, sy = self._selection_start or (0, 0)
        ex, ey = self._selection_end or (sx, sy)
        return min(sx, ex), min(sy, ey), max(sx, ex), max(sy, ey)

    def _video_pos_to_ai(self, pos: QPoint) -> Optional[tuple[int, int]]:
        if self._main_pixmap is None or self._main_pixmap.isNull():
            return None

        label_size = self.main_label.size()
        source_size = self._main_pixmap.size()
        scale = min(
            label_size.width() / max(1, source_size.width()),
            label_size.height() / max(1, source_size.height()),
        )
        displayed_width = round(source_size.width() * scale)
        displayed_height = round(source_size.height() * scale)
        x_offset = max(0, (label_size.width() - displayed_width) // 2)
        y_offset = max(0, (label_size.height() - displayed_height) // 2)
        x = pos.x() - x_offset
        y = pos.y() - y_offset

        if x < 0 or y < 0 or x >= displayed_width or y >= displayed_height:
            return None

        ai_x = clamp(round(x * (AI_COORD_SIZE[0] - 1) / max(1, displayed_width - 1)), 0, AI_COORD_SIZE[0] - 1)
        ai_y = clamp(round(y * (AI_COORD_SIZE[1] - 1) / max(1, displayed_height - 1)), 0, AI_COORD_SIZE[1] - 1)
        return ai_x, ai_y

    def _handle_main_press(self, pos: QPoint):
        ai_pos = self._video_pos_to_ai(pos)
        if not ai_pos:
            return
        self._selection_start = ai_pos
        self._selection_end = ai_pos

    def _handle_main_move(self, pos: QPoint):
        if not self._selection_start:
            return
        ai_pos = self._video_pos_to_ai(pos)
        if not ai_pos:
            return
        self._selection_end = ai_pos
        self._refresh_pixmaps()

    def _handle_main_release(self, pos: QPoint):
        if not self._selection_start:
            return
        ai_pos = self._video_pos_to_ai(pos)
        if ai_pos:
            self._selection_end = ai_pos

        left, top, right, bottom = self._normalized_selection()
        self._selection_start = None
        self._selection_end = None
        self._refresh_pixmaps()

        if right - left >= 24 and bottom - top >= 24:
            self.main_box_selected.emit(left, top, right, bottom)
        else:
            x, y = ai_pos or (left, top)
            self.main_clicked.emit(x, y)

    def _handle_cancel_requested(self):
        self._selection_start = None
        self._selection_end = None
        self.set_ai_tracking_box(None)
        self.cancel_requested.emit()


class CollapsibleSection(QFrame):
    def __init__(self, title: str, expanded: bool = True, parent=None):
        super().__init__(parent)
        self.setObjectName("collapsibleSection")

        self.header = QToolButton()
        self.header.setObjectName("sectionHeader")
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.header.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.header.clicked.connect(self.set_expanded)

        self.body = QFrame()
        self.body.setObjectName("sectionBody")
        self.body.setVisible(expanded)

        self._body_layout = QVBoxLayout(self.body)
        self._body_layout.setContentsMargins(14, 10, 14, 14)
        self._body_layout.setSpacing(10)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header)
        layout.addWidget(self.body)

    def set_expanded(self, expanded: bool):
        self.header.setChecked(expanded)
        self.header.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.body.setVisible(expanded)

    def layout(self):
        return self._body_layout


class ZT30QtDashboard(QMainWindow):
    log_signal = pyqtSignal(str)
    telemetry_signal = pyqtSignal(object, object)
    laser_overlay_signal = pyqtSignal(object)
    ai_tracking_signal = pyqtSignal(object)
    ai_status_signal = pyqtSignal(str)
    media_list_signal = pyqtSignal(object, object)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ZT30 Control")
        self.resize(1440, 900)

        self.client: Optional[ZT30UDPClient] = None
        self.ai_client: Optional[SiyiAITrackingClient] = None
        self.web_client: Optional[ZT30WebClient] = None
        self.connected_host = ""
        self.connected_port = 0
        self.connected_ai_host = ""
        self.connected_ai_port = 0

        self.main_thread: Optional[FFmpegStreamThread] = None
        self.pip_thread: Optional[FFmpegStreamThread] = None
        self.primary_frame: Optional[QImage] = None
        self.video2_frame: Optional[QImage] = None
        self.primary_on_main = True
        self.joystick_thread: Optional[JoystickThread] = None
        self.last_joystick_speed = (None, None)
        self.laser_enabled = False
        self.laser_range: Optional[float] = None
        self.laser_target = None
        self.last_selected_ai_box: Optional[AITrackingBox] = None
        self.current_media_type = 0
        self.media_items = []
        self._updating_sources = False

        self._build_ui()
        self._apply_style()

        self.log_signal.connect(self.log.append)
        self.telemetry_signal.connect(self._apply_telemetry)
        self.laser_overlay_signal.connect(self._apply_laser_overlay)
        self.ai_tracking_signal.connect(self._apply_ai_tracking_box)
        self.ai_status_signal.connect(self._apply_ai_status)
        self.media_list_signal.connect(self._apply_media_list)

        self._ensure_client()

        self.telemetry_timer = QTimer(self)
        self.telemetry_timer.timeout.connect(self.refresh_status)
        self.telemetry_timer.start(1500)

        self.laser_timer = QTimer(self)
        self.laser_timer.timeout.connect(self.refresh_laser_overlay)
        self.laser_timer.start(2500)

    def _build_ui(self):
        root = CockpitBackground()
        self.setCentralWidget(root)

        shell = QVBoxLayout(root)
        shell.setContentsMargins(20, 18, 20, 20)
        shell.setSpacing(14)

        content = QHBoxLayout()
        content.setSpacing(14)
        content.addLayout(self._build_video_column(), 1)
        content.addWidget(self._build_control_panel(), 0)

        shell.addLayout(content, 1)

    def _build_video_column(self):
        column = QVBoxLayout()
        column.setSpacing(12)

        toolbar = QFrame()
        toolbar.setObjectName("toolbar")
        tools = QHBoxLayout(toolbar)
        tools.setContentsMargins(12, 10, 12, 10)
        tools.setSpacing(10)

        tools.addWidget(QLabel("Main"))
        self.main_source_combo = QComboBox()
        self.main_source_combo.addItems(["AI Camera", "Video 1"])
        self.main_source_combo.setCurrentText("AI Camera")
        self.main_source_combo.currentIndexChanged.connect(self.handle_source_change)
        tools.addWidget(self.main_source_combo)

        self.pip_check = QCheckBox("Picture in Picture")
        self.pip_check.setChecked(True)
        self.pip_check.toggled.connect(self.handle_pip_toggle)
        tools.addWidget(self.pip_check)

        self.pip_source_label = QLabel("PiP: Video 2")
        self.pip_source_label.setObjectName("statusBadge")
        tools.addWidget(self.pip_source_label)

        tools.addWidget(QLabel("PiP Size"))
        self.pip_size_slider = QSlider(Qt.Horizontal)
        self.pip_size_slider.setRange(160, 520)
        self.pip_size_slider.setValue(PIP_SIZE_DEFAULT[0])
        self.pip_size_slider.setFixedWidth(120)
        self.pip_size_slider.valueChanged.connect(lambda value: self.video_stage.set_pip_size(value))
        tools.addWidget(self.pip_size_slider)

        self.status_badge = QLabel("Idle")
        self.status_badge.setObjectName("statusBadge")
        tools.addWidget(self.status_badge)

        tools.addStretch(1)

        play = QPushButton("Connect + Play")
        play.clicked.connect(self.start_streams)

        stop = QPushButton("Stop")
        stop.clicked.connect(self.stop_streams)

        tools.addWidget(play)
        tools.addWidget(stop)

        self.video_stage = VideoStage()
        self.video_stage.pip_clicked.connect(self.swap_pip_view)
        self.video_stage.main_clicked.connect(self.track_clicked_point)
        self.video_stage.main_box_selected.connect(self.track_ai_box)
        self.video_stage.cancel_requested.connect(self.cancel_ai_tracking)

        column.addWidget(toolbar)
        column.addWidget(self.video_stage, 1)

        return column

    def _build_control_panel(self):
        wrapper = QFrame()
        wrapper.setObjectName("controlWrapper")
        wrapper.setFixedWidth(470)

        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(8)

        toggle_row = QHBoxLayout()
        toggle_row.setContentsMargins(0, 0, 0, 0)

        self.sidebar_toggle = QToolButton()
        self.sidebar_toggle.setObjectName("sidebarToggle")
        self.sidebar_toggle.setText("Controls  <")
        self.sidebar_toggle.setCheckable(True)
        self.sidebar_toggle.setChecked(True)
        self.sidebar_toggle.clicked.connect(self.toggle_sidebar)

        toggle_row.addStretch(1)
        toggle_row.addWidget(self.sidebar_toggle)
        wrapper_layout.addLayout(toggle_row)

        scroll = QScrollArea()
        scroll.setObjectName("controlScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setFixedWidth(450)

        self.control_scroll = scroll
        self.control_wrapper = wrapper

        panel = QFrame()
        panel.setObjectName("sidePanel")
        panel.setFixedWidth(430)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        layout.addWidget(self._build_connection_controls())
        layout.addWidget(self._build_quick_actions())
        layout.addWidget(self._build_gimbal_controls())
        layout.addWidget(self._build_camera_controls())
        layout.addWidget(self._build_media_controls())
        layout.addWidget(self._build_ai_controls())
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
        wrapper_layout.addWidget(scroll, 1)

        return wrapper

    def toggle_sidebar(self):
        expanded = self.sidebar_toggle.isChecked()
        self.control_scroll.setVisible(expanded)
        self.control_wrapper.setFixedWidth(470 if expanded else 48)
        self.sidebar_toggle.setText("Controls  <" if expanded else ">")

    def _build_connection_controls(self):
        box = self._section("Connection")
        grid = QGridLayout()

        self.host_edit = QLineEdit(DEFAULT_IP)
        self.host_edit.setMinimumWidth(180)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(DEFAULT_PORT)
        self.port_spin.setMinimumWidth(100)

        reconnect = QPushButton("Connect UDP")
        reconnect.clicked.connect(lambda: self._ensure_client(force=True))

        connect_play = QPushButton("Connect + Play")
        connect_play.clicked.connect(self.start_streams)

        grid.addWidget(QLabel("Camera IP"), 0, 0)
        grid.addWidget(self.host_edit, 0, 1, 1, 2)

        grid.addWidget(QLabel("UDP Port"), 1, 0)
        grid.addWidget(self.port_spin, 1, 1)
        grid.addWidget(reconnect, 1, 2)

        grid.addWidget(connect_play, 2, 1, 1, 2)

        box.layout().addLayout(grid)
        return box

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
            btn.clicked.connect(
                lambda _checked=False, value=mode: self.run_command(
                    f"{value} mode",
                    lambda: self.client.set_motion_mode(value),
                )
            )
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
        set_angle.clicked.connect(
            lambda: self.run_command(
                "set angle",
                lambda: self.client.set_angle(self.yaw_spin.value(), self.pitch_spin.value()),
            )
        )

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

        zoom_out = self._press_button("Zoom -", self.client_zoom_out, self.client_zoom_stop)
        zoom_in = self._press_button("Zoom +", self.client_zoom_in, self.client_zoom_stop)

        self.zoom_spin = QDoubleSpinBox()
        self.zoom_spin.setRange(1.0, 30.9)
        self.zoom_spin.setSingleStep(0.5)
        self.zoom_spin.setValue(4.5)

        set_zoom = QPushButton("Set Zoom")
        set_zoom.clicked.connect(
            lambda: self.run_command(
                "set zoom",
                lambda: self.client.absolute_zoom(self.zoom_spin.value()),
            )
        )

        zoom_row.addWidget(zoom_out)
        zoom_row.addWidget(zoom_in)
        zoom_row.addWidget(self.zoom_spin)
        zoom_row.addWidget(set_zoom)

        box.layout().addLayout(zoom_row)

        focus_row = QHBoxLayout()
        focus_row.addWidget(self._press_button("Near Focus", self.client_focus_near, self.client_focus_stop))
        focus_row.addWidget(self._press_button("Far Focus", self.client_focus_far, self.client_focus_stop))
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

        self.temp_x_spin = QSpinBox()
        self.temp_x_spin.setRange(0, 1920)
        self.temp_x_spin.setValue(320)

        self.temp_y_spin = QSpinBox()
        self.temp_y_spin.setRange(0, 1080)
        self.temp_y_spin.setValue(256)

        temp_coord_row = QHBoxLayout()
        temp_coord_row.addWidget(QLabel("Point X"))
        temp_coord_row.addWidget(self.temp_x_spin)
        temp_coord_row.addWidget(QLabel("Y"))
        temp_coord_row.addWidget(self.temp_y_spin)

        box.layout().addLayout(temp_coord_row)

        temp_row = QHBoxLayout()

        point_temp = QPushButton("Point")
        point_temp.clicked.connect(
            lambda: self.run_command(
                "point temperature",
                lambda: self.client.request_temperature_point(
                    self.temp_x_spin.value(),
                    self.temp_y_spin.value(),
                ),
            )
        )

        full_temp = QPushButton("Full")
        full_temp.clicked.connect(
            lambda: self.run_command(
                "full temperature",
                self.client.request_temperature_full_image,
            )
        )

        temp_row.addWidget(point_temp)
        temp_row.addWidget(full_temp)

        box.layout().addLayout(temp_row)

        return box

    def _build_laser_controls(self):
        box = self._section("Laser Rangefinder")
        row = QHBoxLayout()

        laser_on = QPushButton("On")
        laser_on.clicked.connect(lambda: self.set_laser_enabled(True))

        laser_off = QPushButton("Off")
        laser_off.clicked.connect(lambda: self.set_laser_enabled(False))

        measure = QPushButton("Range")
        measure.clicked.connect(self.refresh_laser_overlay)

        target = QPushButton("GPS")
        target.clicked.connect(self.refresh_laser_overlay)

        row.addWidget(laser_on)
        row.addWidget(laser_off)
        row.addWidget(measure)
        row.addWidget(target)

        box.layout().addLayout(row)
        return box

    def _build_media_controls(self):
        box = self._section("Media")

        row = QHBoxLayout()

        photos = QPushButton("Photos")
        photos.clicked.connect(lambda: self.load_media(0))

        videos = QPushButton("Videos")
        videos.clicked.connect(lambda: self.load_media(1))

        refresh = QPushButton("Refresh")
        refresh.clicked.connect(lambda: self.load_media(self.current_media_type))

        row.addWidget(photos)
        row.addWidget(videos)
        row.addWidget(refresh)
        box.layout().addLayout(row)

        self.media_table = QTableWidget(0, 2)
        self.media_table.setHorizontalHeaderLabels(["Name", "URL"])
        self.media_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.media_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.media_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.media_table.verticalHeader().setVisible(False)
        self.media_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.media_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.media_table.setFixedHeight(150)
        self.media_table.doubleClicked.connect(lambda _index: self.open_selected_media())
        box.layout().addWidget(self.media_table)

        action_row = QHBoxLayout()

        open_btn = QPushButton("Open")
        open_btn.clicked.connect(self.open_selected_media)

        download_btn = QPushButton("Download")
        download_btn.clicked.connect(self.download_selected_media)

        action_row.addWidget(open_btn)
        action_row.addWidget(download_btn)
        box.layout().addLayout(action_row)

        self.media_status_label = QLabel("Media: ready")
        self.media_status_label.setObjectName("metricLabel")
        box.layout().addWidget(self.media_status_label)

        return box

    def _build_ai_controls(self):
        box = self._section("AI Tracking")
        grid = QGridLayout()

        self.ai_host_edit = QLineEdit(DEFAULT_AI_IP)
        self.ai_host_edit.setMinimumWidth(180)

        self.ai_port_spin = QSpinBox()
        self.ai_port_spin.setRange(1, 65535)
        self.ai_port_spin.setValue(DEFAULT_PORT)
        self.ai_port_spin.setMinimumWidth(100)

        ai_connect = QPushButton("Connect AI")
        ai_connect.clicked.connect(lambda: self._ensure_ai_client(force=True))

        grid.addWidget(QLabel("AI IP"), 0, 0)
        grid.addWidget(self.ai_host_edit, 0, 1, 1, 2)
        grid.addWidget(QLabel("Port"), 1, 0)
        grid.addWidget(self.ai_port_spin, 1, 1)
        grid.addWidget(ai_connect, 1, 2)

        box.layout().addLayout(grid)

        row = QHBoxLayout()

        ai_on = QPushButton("Rec On")
        ai_on.clicked.connect(lambda: self.set_ai_recognition(True))

        ai_off = QPushButton("Rec Off")
        ai_off.clicked.connect(lambda: self.set_ai_recognition(False))

        ai_status = QPushButton("Status")
        ai_status.clicked.connect(self.refresh_ai_status)

        row.addWidget(ai_on)
        row.addWidget(ai_off)
        row.addWidget(ai_status)
        box.layout().addLayout(row)

        track_row = QHBoxLayout()

        center = QPushButton("Track Center")
        center.clicked.connect(lambda: self.track_ai_point(AI_COORD_SIZE[0] // 2, AI_COORD_SIZE[1] // 2))

        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.cancel_ai_tracking)

        self.ai_overlay_check = QCheckBox("Overlay")
        self.ai_overlay_check.setChecked(True)
        self.ai_overlay_check.toggled.connect(self.toggle_ai_overlay)

        track_row.addWidget(center)
        track_row.addWidget(cancel)
        track_row.addWidget(self.ai_overlay_check)
        box.layout().addLayout(track_row)

        self.ai_status_label = QLabel("AI: idle")
        self.ai_status_label.setObjectName("metricLabel")
        box.layout().addWidget(self.ai_status_label)

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
        return CollapsibleSection(title, expanded=False)

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
        btn.pressed.connect(
            lambda: self.rotate(
                yaw_dir * self.speed_slider.value(),
                pitch_dir * self.speed_slider.value(),
            )
        )
        btn.released.connect(self.stop_rotation)
        return btn

    def _press_button(self, label: str, start: Callable[[], None], stop: Callable[[], None]):
        btn = QPushButton(label)
        btn.pressed.connect(start)
        btn.released.connect(stop)
        return btn

    def _ensure_client(self, force: bool = False):
        host = self.host_edit.text().strip()
        port = self.port_spin.value()

        if not force and self.client and self.connected_host == host and self.connected_port == port:
            return True

        self.stop_joystick()

        if self.client:
            try:
                self.client.close()
            except Exception:
                pass

        try:
            self.client = ZT30UDPClient(host, port)
            self.connected_host = host
            self.connected_port = port
            self.log_message(f"UDP ready: {host}:{port}")
            return True

        except Exception as exc:
            self.client = None
            self.log_message(f"UDP connect error: {exc}")
            return False

    def _ensure_web_client(self, force: bool = False):
        host = self.host_edit.text().strip()
        base_url = f"http://{host}:82//cgi-bin/media.cgi"

        if not force and self.web_client and getattr(self.web_client, "base_url", "") == base_url.rstrip("/"):
            return True

        try:
            self.web_client = ZT30WebClient(base_url, timeout=5.0)
            return True
        except Exception as exc:
            self.web_client = None
            self.log_message(f"media client: ERROR {exc}")
            return False

    def _ensure_ai_client(self, force: bool = False):
        host = self.ai_host_edit.text().strip()
        port = self.ai_port_spin.value()

        if not force and self.ai_client and self.connected_ai_host == host and self.connected_ai_port == port:
            return True

        if self.ai_client:
            try:
                self.ai_client.close()
            except Exception:
                pass

        try:
            self.ai_client = SiyiAITrackingClient(host, port)
            self.connected_ai_host = host
            self.connected_ai_port = port
            self.log_message(f"AI UDP ready: {host}:{port}")
            return True

        except Exception as exc:
            self.ai_client = None
            self.log_message(f"AI UDP connect error: {exc}")
            return False

    def start_streams(self):
        self.stop_streams()

        try:
            primary_source = self._source_spec(self.main_source_combo.currentText())
            video2_source = self._source_spec("Video 2")
        except Exception as exc:
            self.log_message(f"video source: ERROR {exc}")
            return

        self.primary_frame = None
        self.video2_frame = None
        self.primary_on_main = True

        self.main_thread = self._start_stream(
            primary_source["url"],
            *STREAM_SIZE,
            lambda image: self._handle_source_frame("primary", image),
            transport=primary_source["transport"],
        )

        self.pip_thread = self._start_stream(
            video2_source["url"],
            *STREAM_SIZE,
            lambda image: self._handle_source_frame("video2", image),
            transport=video2_source["transport"],
        )

        self._render_video_layout()

    def _source_spec(self, name: str):
        name = name.strip()
        camera_host = self.host_edit.text().strip()

        if name == "AI Camera":
            if not self._ensure_ai_client():
                raise RuntimeError("AI client not ready")
            self._set_ai_rtsp_enabled(True)
            return {
                "name": name,
                "url": ai_rtsp_url(self.ai_host_edit.text().strip()),
                "transport": "udp",
            }

        if name == "Video 1":
            return {"name": name, "url": rtsp_url(camera_host, 1), "transport": "tcp"}

        if name == "Video 2":
            return {"name": name, "url": rtsp_url(camera_host, 2), "transport": "tcp"}

        raise ValueError(f"unknown source {name!r}")

    def _start_stream(self, url: str, width: int, height: int, frame_slot, transport: str = "tcp"):
        thread = FFmpegStreamThread(url, width, height, transport, self)
        thread.frame_ready.connect(frame_slot)
        thread.status_changed.connect(self.status_badge.setText)
        thread.error.connect(lambda text: self.log_message(f"stream error: {text}"))
        thread.start()
        self.log_message(f"Playing {url}")
        return thread

    def _handle_source_frame(self, source: str, image: QImage):
        if source == "primary":
            self.primary_frame = image
        elif source == "video2":
            self.video2_frame = image
        self._render_video_layout()

    def _render_video_layout(self):
        main_frame = self.primary_frame if self.primary_on_main else self.video2_frame
        pip_frame = self.video2_frame if self.primary_on_main else self.primary_frame

        if main_frame is not None:
            self.video_stage.set_main_frame(main_frame)

        pip_enabled = self.pip_check.isChecked() and pip_frame is not None
        self.video_stage.set_pip_enabled(pip_enabled)
        if pip_enabled:
            self.video_stage.set_pip_frame(pip_frame)

    def stop_streams(self):
        if self.main_thread:
            self.main_thread.stop()
            self.main_thread = None

        if self.pip_thread:
            self.pip_thread.stop()
            self.pip_thread = None

        self.status_badge.setText("Idle")
        self.video_stage.clear_main("Select Connect + Play to start the stream")
        self.video_stage.set_pip_enabled(False)
        self.primary_frame = None
        self.video2_frame = None

        if self.ai_client:
            self._set_ai_rtsp_enabled(False)

    def _set_ai_rtsp_enabled(self, enabled: bool):
        try:
            result = self.ai_client.set_rtsp_stream_enabled(enabled)
            self.log_message(f"AI RTSP {'on' if enabled else 'off'}: {result}")
            return result
        except Exception as exc:
            self.log_message(f"AI RTSP {'on' if enabled else 'off'}: ERROR {exc}")
            return None

    def handle_pip_toggle(self, enabled: bool):
        self._render_video_layout()

    def handle_source_change(self):
        if self._updating_sources:
            return
        if self.main_thread:
            self.start_streams()

    def swap_pip_view(self):
        if not self.pip_check.isChecked():
            return

        self.primary_on_main = not self.primary_on_main
        self._render_video_layout()
        main_name = self.main_source_combo.currentText() if self.primary_on_main else "Video 2"
        self.log_message(f"PiP clicked: main view is now {main_name}")

    def set_laser_enabled(self, enabled: bool):
        if not self._ensure_client():
            return

        threading.Thread(
            target=self._laser_enable_worker,
            args=(enabled,),
            daemon=True,
        ).start()

    def _laser_enable_worker(self, enabled: bool):
        try:
            result = self.client.set_laser(enabled)
            self.laser_enabled = enabled

            if enabled:
                self.log_message(f"laser on: {result if result is not None else 'OK'}")
                self._update_laser_measurement()
            else:
                self.laser_range = None
                self.laser_target = None
                self.laser_overlay_signal.emit([])
                self.log_message(f"laser off: {result if result is not None else 'OK'}")

        except Exception as exc:
            self.log_message(f"laser {'on' if enabled else 'off'}: ERROR {exc}")

    def refresh_laser_overlay(self):
        if not self.laser_enabled:
            return

        if not self._ensure_client():
            return

        threading.Thread(target=self._update_laser_measurement, daemon=True).start()

    def _update_laser_measurement(self):
        try:
            self.laser_range = self.client.request_laser_range()
            self.laser_target = self.client.request_laser_target_latlon()
            self.laser_overlay_signal.emit(self._laser_overlay_lines())
            self.log_message("laser overlay: updated")
        except Exception as exc:
            self.log_message(f"laser overlay: ERROR {exc}")

    def _laser_overlay_lines(self):
        lines = ["LASER RANGEFINDER"]

        if self.laser_range is None:
            lines.append("Range: -")
        else:
            lines.append(f"Range: {self.laser_range:.1f} m")

        if self.laser_target:
            lines.append(f"Lat: {self.laser_target['lat']:.7f}")
            lines.append(f"Lon: {self.laser_target['lon']:.7f}")
        else:
            lines.append("Lat/Lon: -")

        return lines

    def _apply_laser_overlay(self, lines):
        if lines:
            self.video_stage.set_laser_overlay(lines)
        else:
            self.video_stage.clear_laser_overlay()

    def load_media(self, media_type: int):
        if not self._ensure_web_client(force=True):
            return

        self.current_media_type = int(media_type)
        label = "photos" if media_type == 0 else "videos"
        self.media_status_label.setText(f"Media: loading {label}...")

        threading.Thread(
            target=self._load_media_worker,
            args=(int(media_type),),
            daemon=True,
        ).start()

    def _load_media_worker(self, media_type: int):
        try:
            dirs = self.web_client.get_directories(media_type)
            if not dirs.get("success"):
                self.media_list_signal.emit([], f"Media: {dirs.get('message', 'directory error')}")
                return

            directories = (dirs.get("data") or {}).get("directories") or []
            if not directories:
                self.media_list_signal.emit([], "Media: no directory found")
                return

            path = directories[0].get("path", "")
            count_data = self.web_client.get_media_count(media_type, path)
            total = ((count_data.get("data") or {}).get("count") if count_data.get("success") else "?")
            listing = self.web_client.get_media_list(media_type, path, 0, 200)

            if not listing.get("success"):
                self.media_list_signal.emit([], f"Media: {listing.get('message', 'list error')}")
                return

            items = (listing.get("data") or {}).get("list") or []
            status = f"Media: {path} | {len(items)}/{total}"
            self.media_list_signal.emit(items, status)
        except Exception as exc:
            self.media_list_signal.emit([], f"Media: ERROR {exc}")

    def _apply_media_list(self, items, status):
        self.media_items = list(items or [])
        self.media_table.setRowCount(len(self.media_items))

        for row, item in enumerate(self.media_items):
            name = item.get("name", "Unnamed")
            url = item.get("url", "")
            name_item = QTableWidgetItem(name)
            name_item.setData(Qt.UserRole, item)
            url_item = QTableWidgetItem(url)
            url_item.setData(Qt.UserRole, item)
            self.media_table.setItem(row, 0, name_item)
            self.media_table.setItem(row, 1, url_item)

        self.media_status_label.setText(str(status))

    def _selected_media_item(self):
        rows = self.media_table.selectionModel().selectedRows() if hasattr(self, "media_table") else []
        if not rows:
            return None
        row = rows[0].row()
        if row < 0 or row >= len(self.media_items):
            return None
        return self.media_items[row]

    def open_selected_media(self):
        item = self._selected_media_item()
        if not item:
            self.media_status_label.setText("Media: select a file")
            return

        url = item.get("url")
        if not self._is_allowed_media_url(url):
            self.media_status_label.setText("Media: invalid URL")
            return

        webbrowser.open(url)
        self.media_status_label.setText(f"Media: opened {item.get('name', 'file')}")

    def download_selected_media(self):
        item = self._selected_media_item()
        if not item:
            self.media_status_label.setText("Media: select a file")
            return

        url = item.get("url")
        if not self._is_allowed_media_url(url):
            self.media_status_label.setText("Media: invalid URL")
            return

        default_name = item.get("name") or Path(urlparse(url).path).name or "zt30_media"
        target, _ = QFileDialog.getSaveFileName(self, "Download Media", str(Path.home() / "Downloads" / default_name))
        if not target:
            return

        self.media_status_label.setText(f"Media: downloading {default_name}...")
        threading.Thread(
            target=self._download_media_worker,
            args=(url, target),
            daemon=True,
        ).start()

    def _download_media_worker(self, url: str, target: str):
        try:
            with urlopen(url, timeout=20) as response, open(target, "wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 256)
            self.log_message(f"media download: {target}")
            self.media_list_signal.emit(self.media_items, f"Media: downloaded {Path(target).name}")
        except Exception as exc:
            self.media_list_signal.emit(self.media_items, f"Media: download ERROR {exc}")

    def _is_allowed_media_url(self, url: Optional[str]) -> bool:
        if not url:
            return False
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        return parsed.scheme in ("http", "https") and parsed.hostname == self.host_edit.text().strip()

    def run_ai_command(self, label: str, command: Callable):
        if not self._ensure_ai_client():
            self.log_message(f"{label}: AI not connected")
            return

        threading.Thread(
            target=self._ai_command_worker,
            args=(label, command),
            daemon=True,
        ).start()

    def _ai_command_worker(self, label: str, command: Callable):
        try:
            result = command()
            self.log_message(f"{label}: {result if result is not None else 'OK'}")
        except Exception as exc:
            self.log_message(f"{label}: ERROR {exc}")

    def set_ai_recognition(self, enabled: bool):
        self.run_ai_command(
            "AI recognition on" if enabled else "AI recognition off",
            lambda: self._set_ai_recognition_worker(enabled),
        )

    def _set_ai_recognition_worker(self, enabled: bool):
        result = self.ai_client.set_recognition_enabled(enabled)
        self.ai_status_signal.emit(f"AI: recognition {self._onoff(result)}")
        return result

    def refresh_ai_status(self):
        self.run_ai_command("AI status", self._read_ai_status)

    def _read_ai_status(self):
        firmware = self.ai_client.request_firmware_version()
        recognition = self.ai_client.get_recognition_enabled()
        tracking = self.ai_client.get_tracking_status()
        stream = self.ai_client.get_coordinate_stream_status()
        text = f"AI: fw {firmware or '-'} | rec {self._onoff(recognition)} | track {self._onoff(tracking)} | stream {stream}"
        self.ai_status_signal.emit(text)
        return text

    def track_clicked_point(self, x: int, y: int):
        if not hasattr(self, "ai_overlay_check"):
            return
        self.track_ai_point(x, y)

    def track_ai_point(self, x: int, y: int):
        overlay_enabled = self.ai_overlay_check.isChecked()
        self.run_ai_command("AI track point", lambda: self._track_ai_point_worker(x, y, overlay_enabled))

    def _track_ai_point_worker(self, x: int, y: int, overlay_enabled: bool):
        recognition = self._prepare_ai_track()
        result = self.ai_client.track_point(x, y)
        if overlay_enabled:
            time.sleep(0.10)
            self._start_ai_overlay_worker()
        return {"recognition": recognition, "track_result": result, "x": x, "y": y}

    def track_ai_box(self, left: int, top: int, right: int, bottom: int):
        self.last_selected_ai_box = None
        self.ai_tracking_signal.emit(None)
        self.ai_status_signal.emit(f"AI: ROI sent | {left},{top} - {right},{bottom}")
        overlay_enabled = self.ai_overlay_check.isChecked()
        self.run_ai_command(
            "AI track box",
            lambda: self._track_ai_box_worker(left, top, right, bottom, overlay_enabled),
        )

    def _track_ai_box_worker(self, left: int, top: int, right: int, bottom: int, overlay_enabled: bool):
        recognition = self._prepare_ai_track()
        result = self.ai_client.track_box(left, top, right, bottom)
        if overlay_enabled:
            time.sleep(0.10)
            self._start_ai_overlay_worker()
        return {
            "recognition": recognition,
            "track_result": result,
            "box": (left, top, right, bottom),
        }

    def _prepare_ai_track(self):
        try:
            self.ai_client.stop_coordinate_listener()
        except Exception:
            pass
        try:
            self.ai_client.set_coordinate_stream_enabled(False)
        except Exception:
            pass
        try:
            self.ai_client.cancel_tracking()
        except Exception:
            pass
        time.sleep(0.15)
        try:
            self.ai_client.set_rtsp_stream_enabled(True)
        except Exception:
            pass
        return self.ai_client.set_recognition_enabled(True)

    def cancel_ai_tracking(self):
        self.run_ai_command("AI cancel tracking", self._cancel_ai_tracking_worker)

    def _cancel_ai_tracking_worker(self):
        try:
            self.ai_client.stop_coordinate_listener()
            self.ai_client.set_coordinate_stream_enabled(False)
        except Exception:
            pass
        result = self.ai_client.cancel_tracking()
        self.last_selected_ai_box = None
        self.ai_tracking_signal.emit(None)
        self.ai_status_signal.emit("AI: standby")
        return result

    def toggle_ai_overlay(self, enabled: bool):
        if enabled:
            self.run_ai_command("AI overlay on", self._start_ai_overlay_worker)
        else:
            self.run_ai_command("AI overlay off", self._stop_ai_overlay_worker)

    def _start_ai_overlay_worker(self):
        stream_enabled = self.ai_client.set_coordinate_stream_enabled(True)
        self.ai_client.start_coordinate_listener(
            lambda box: self.ai_tracking_signal.emit(box),
            lambda exc: self.log_message(f"AI overlay listener: ERROR {exc}"),
        )
        return stream_enabled

    def _stop_ai_overlay_worker(self):
        try:
            stream_enabled = self.ai_client.set_coordinate_stream_enabled(False)
        finally:
            self.ai_client.stop_coordinate_listener()
            self.ai_tracking_signal.emit(None)
        return stream_enabled

    def _apply_ai_tracking_box(self, box):
        if box and hasattr(self, "ai_status_label"):
            if box.target_id == 255:
                self.ai_status_label.setText(f"AI: camera feedback | {box.x},{box.y}")
            else:
                self.ai_status_label.setText(f"AI: {box.target_type} | {box.state} | {box.x},{box.y}")
        elif box is None:
            self.video_stage.set_ai_tracking_box(None)
            self.last_selected_ai_box = None

    def _apply_ai_status(self, text: str):
        if hasattr(self, "ai_status_label"):
            self.ai_status_label.setText(text)

    @staticmethod
    def _is_unhelpful_full_frame_ai_box(box: AITrackingBox) -> bool:
        return (
            box.target_id == 255
            and box.track_state == 4
            and box.width >= AI_COORD_SIZE[0] * 0.90
            and box.height >= AI_COORD_SIZE[1] * 0.90
        )

    @staticmethod
    def _onoff(value: Optional[bool]) -> str:
        if value is None:
            return "-"
        return "on" if value else "off"

    def run_command(self, label: str, command: Callable):
        if not self._ensure_client():
            self.log_message(f"{label}: not connected")
            return

        threading.Thread(
            target=self._command_worker,
            args=(label, command),
            daemon=True,
        ).start()

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
            QMessageBox.warning(
                self,
                "View not available",
                "This camera view is not available in the SDK mapping.",
            )
            return

        self.run_command("camera view", lambda: self.client.set_image_mode(mode))

    def apply_palette(self):
        palette = THERMAL_NAMES[self.palette_combo.currentText()]

        if palette not in THERMAL_PALETTES.values():
            QMessageBox.warning(
                self,
                "Palette not available",
                "This thermal palette is not available in the SDK mapping.",
            )
            return

        self.run_command("thermal palette", lambda: self.client.set_thermal_palette(palette))

    def refresh_status(self):
        if not self._ensure_client():
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
        if not self._ensure_client():
            self.joystick_check.setChecked(False)
            return

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

        if hasattr(self, "joystick_status"):
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

        threading.Thread(
            target=lambda: self.client.rotate_speed(yaw, pitch),
            daemon=True,
        ).start()

    def log_message(self, text: str):
        self.log_signal.emit(text)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Refresh):
            self.refresh_status()
            return

        super().keyPressEvent(event)

    def closeEvent(self, event):
        self.laser_timer.stop()
        self.stop_joystick()
        self.stop_streams()

        if self.client:
            self.client.close()

        if self.ai_client:
            self.ai_client.close()

        event.accept()

    def _apply_style(self):
        QApplication.instance().setStyleSheet(
            """
            QWidget {
                background: transparent;
                color: #f2f2ef;
                font-family: "Liberation Sans", "Inter", "Segoe UI", Arial, sans-serif;
                font-size: 13px;
            }

            QMainWindow {
                background: #000000;
            }

            QFrame#toolbar,
            QFrame#sidePanel,
            QFrame#controlWrapper,
            QFrame#collapsibleSection {
                background: rgba(6, 10, 11, 188);
                border: 1px solid rgba(93, 129, 130, 118);
                border-radius: 8px;
            }

            QFrame#toolbar {
                max-height: 58px;
            }

            QFrame#sidePanel {
                background: rgba(5, 9, 10, 205);
            }

            QFrame#collapsibleSection {
                font-weight: 900;
            }

            QFrame#sectionBody {
                border: none;
                background: transparent;
                border-top: 1px solid rgba(93, 129, 130, 70);
                border-radius: 0px;
            }

            QToolButton#sectionHeader {
                color: #f2f2ef;
                background: rgba(7, 12, 13, 190);
                border: none;
                border-radius: 8px;
                padding: 10px 12px;
                font-weight: 900;
                text-align: left;
            }

            QToolButton#sectionHeader:hover {
                background: rgba(13, 25, 26, 220);
                border: none;
            }

            QLineEdit,
            QSpinBox,
            QDoubleSpinBox,
            QComboBox {
                color: #f2f2ef;
                background: rgba(6, 9, 10, 220);
                border: 1px solid rgba(98, 122, 122, 125);
                border-radius: 6px;
                padding: 7px 9px;
                min-height: 20px;
                selection-background-color: #58f7e8;
            }

            QLineEdit:focus,
            QSpinBox:focus,
            QDoubleSpinBox:focus,
            QComboBox:focus {
                border-color: rgba(207, 255, 251, 220);
                background: rgba(9, 18, 19, 230);
            }

            QComboBox::drop-down {
                border: none;
                width: 24px;
            }

            QComboBox QAbstractItemView {
                color: #f2f2ef;
                background: #071012;
                border: 1px solid rgba(130, 190, 190, 150);
                selection-background-color: #58f7e8;
                selection-color: #061010;
            }

            QPushButton, QToolButton {
                color: #f2f2ef;
                background: rgba(10, 14, 15, 205);
                border: 1px solid rgba(98, 122, 122, 112);
                border-radius: 6px;
                padding: 9px 13px;
                font-weight: 900;
                min-height: 32px;
            }

            QPushButton:hover, QToolButton:hover {
                color: #ffffff;
                background: #1a1f1f;
                border-color: #d7dbdb;
            }

            QPushButton:pressed, QToolButton:pressed {
                color: #061010;
                background: #ffffff;
                border-color: #ffffff;
            }

            QPushButton:disabled, QToolButton:disabled {
                color: #657171;
                background: #101515;
                border-color: #252d2d;
            }

            QCheckBox {
                color: #dce8e8;
                spacing: 8px;
            }

            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 1px solid rgba(170, 205, 205, 160);
                background: rgba(0, 0, 0, 155);
            }

            QCheckBox::indicator:hover {
                border-color: #ffffff;
            }

            QCheckBox::indicator:checked {
                background: #58f7e8;
                border-color: #cffffb;
            }

            QTableWidget {
                color: #f2f2ef;
                background: rgba(6, 9, 10, 185);
                border: 1px solid rgba(98, 122, 122, 112);
                border-radius: 6px;
                gridline-color: rgba(93, 129, 130, 90);
                selection-background-color: rgba(88, 247, 232, 190);
                selection-color: #061010;
            }

            QHeaderView::section {
                color: #061010;
                background: #58f7e8;
                border: none;
                padding: 5px 7px;
                font-weight: 900;
            }

            QScrollArea,
            QFrame#controlScroll {
                background: transparent;
                border: none;
            }

            #sidebarToggle {
                color: #aebbbb;
                background: rgba(6, 10, 11, 190);
                border: 1px solid rgba(93, 129, 130, 118);
                padding: 8px 10px;
            }

            #sidebarToggle:hover {
                color: #ffffff;
                border-color: rgba(207, 255, 252, 190);
            }

            #videoStage {
                background: rgba(0, 0, 0, 185);
                border-radius: 8px;
                border: 1px solid rgba(112, 160, 160, 130);
            }

            #mainVideo {
                background: #000000;
                color: #86a2a2;
                border-radius: 8px;
                font-size: 18px;
            }

            #pipVideo {
                background: #000000;
                color: #f2f2ef;
                border: 2px solid #f2f2ef;
                border-radius: 7px;
                margin: 18px;
            }

            #statusBadge {
                color: #061010;
                background: #58f7e8;
                border-radius: 5px;
                padding: 5px 9px;
                font-weight: 900;
            }

            #metricLabel {
                color: #aebbbb;
                font-size: 12px;
            }

            #metricValue {
                color: #ffffff;
                font-size: 20px;
                font-weight: 900;
            }

            #log {
                color: #aebbbb;
                background: rgba(6, 9, 10, 220);
                border: 1px solid rgba(98, 122, 122, 112);
                border-radius: 6px;
            }

            QScrollBar:vertical {
                background: rgba(4, 7, 8, 160);
                width: 12px;
                margin: 0px;
            }

            QScrollBar::handle:vertical {
                background: rgba(134, 162, 162, 150);
                border-radius: 5px;
                min-height: 30px;
            }

            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }

            QSlider::groove:horizontal {
                height: 6px;
                background: rgba(6, 9, 10, 220);
                border: 1px solid rgba(98, 122, 122, 112);
                border-radius: 3px;
            }

            QSlider::handle:horizontal {
                background: #58f7e8;
                width: 16px;
                margin: -5px 0;
                border-radius: 8px;
            }

            QToolTip {
                color: #f2f2ef;
                background: #071012;
                border: 1px solid #58f7e8;
                padding: 6px;
            }
            """
        )


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ZT30 Control")
    app.setFont(QFont("Inter", 10))

    window = ZT30QtDashboard()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
