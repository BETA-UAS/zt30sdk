#!/usr/bin/env python3
"""Modern Tkinter dashboard for SIYI ZT30 control and RTSP monitoring."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from siyi_zt30 import DEFAULT_IP, DEFAULT_PORT, ZT30UDPClient
from siyi_zt30.constants import IMAGE_MODES, THERMAL_PALETTES


def rtsp_url(host: str, stream: int) -> str:
    return f"rtsp://{host}:8554/video{stream}"


class StreamPanel(ttk.Frame):
    FRAME_WIDTH = 640
    FRAME_HEIGHT = 360

    def __init__(self, parent, title: str, url_var: tk.StringVar, log):
        super().__init__(parent, style="Panel.TFrame", padding=10)
        self.title = title
        self.url_var = url_var
        self.log = log
        self.process = None
        self.reader_thread = None
        self.stderr_thread = None
        self.stop_event = threading.Event()
        self.photo = None
        self.is_playing = False

        top = ttk.Frame(self, style="Panel.TFrame")
        top.pack(fill=tk.X)
        ttk.Label(top, text=title, style="PanelTitle.TLabel").pack(side=tk.LEFT)
        self.status_var = tk.StringVar(value="Stopped")
        ttk.Label(top, textvariable=self.status_var, style="Muted.TLabel").pack(side=tk.RIGHT)

        self.video = tk.Frame(self, background="#05070a", highlightbackground="#2f3844", highlightthickness=1)
        self.video.pack(fill=tk.BOTH, expand=True, pady=(8, 8))
        self.video_label = ttk.Label(
            self.video,
            text="RTSP",
            anchor=tk.CENTER,
            style="StreamPlaceholder.TLabel",
        )
        self.video_label.pack(fill=tk.BOTH, expand=True)

        ttk.Entry(self, textvariable=url_var).pack(fill=tk.X, pady=(0, 8))

        controls = ttk.Frame(self, style="Panel.TFrame")
        controls.pack(fill=tk.X)
        ttk.Button(controls, text="Play", command=self.play, style="Accent.TButton").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(controls, text="Stop", command=self.stop).pack(side=tk.LEFT, padx=6)
        ttk.Button(controls, text="Open FFplay", command=self.open_external).pack(side=tk.RIGHT)

    def play(self):
        if shutil.which("ffmpeg") is None:
            self.status_var.set("ffmpeg not found")
            self.log(f"{self.title}: ffmpeg not found")
            return
        if Image is None or ImageTk is None:
            self.status_var.set("Pillow not installed")
            self.log(f"{self.title}: install Pillow to display ffmpeg frames")
            return

        self.stop()
        self.stop_event.clear()
        url = self.url_var.get().strip()
        frame_bytes = self.FRAME_WIDTH * self.FRAME_HEIGHT * 3
        vf = (
            f"fps=15,scale={self.FRAME_WIDTH}:{self.FRAME_HEIGHT}:"
            f"force_original_aspect_ratio=decrease,"
            f"pad={self.FRAME_WIDTH}:{self.FRAME_HEIGHT}:(ow-iw)/2:(oh-ih)/2"
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
            url,
            "-an",
            "-vf",
            vf,
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ]
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=frame_bytes * 2,
            )
            self.is_playing = True
            self.status_var.set("Playing")
            self.video_label.configure(text="Connecting...", image="")
            self.reader_thread = threading.Thread(target=self._read_frames, args=(self.process, frame_bytes), daemon=True)
            self.stderr_thread = threading.Thread(target=self._read_stderr, args=(self.process,), daemon=True)
            self.reader_thread.start()
            self.stderr_thread.start()
            self.log(f"{self.title}: ffmpeg playing {url}")
        except Exception as exc:
            self.status_var.set("Play failed")
            self.log(f"{self.title}: ERROR {exc}")

    def _read_frames(self, process, frame_bytes: int):
        if process.stdout is None:
            return
        while not self.stop_event.is_set():
            frame = self._read_exact(process, frame_bytes)
            if frame is None:
                break
            self.after(0, self._show_frame, frame)
        self.after(0, self._stream_finished)

    def _read_exact(self, process, size: int):
        if process.stdout is None:
            return None
        data = bytearray()
        while len(data) < size and not self.stop_event.is_set():
            chunk = process.stdout.read(size - len(data))
            if not chunk:
                return None
            data.extend(chunk)
        return bytes(data)

    def _read_stderr(self, process):
        if process.stderr is None:
            return
        last_line = ""
        for raw in iter(process.stderr.readline, b""):
            if self.stop_event.is_set():
                return
            text = raw.decode("utf-8", errors="replace").strip()
            if text:
                last_line = text
        if last_line and not self.stop_event.is_set():
            self.after(0, lambda: self.log(f"{self.title}: ffmpeg {last_line}"))

    def _show_frame(self, frame: bytes):
        if not self.is_playing:
            return
        image = Image.frombytes("RGB", (self.FRAME_WIDTH, self.FRAME_HEIGHT), frame)
        self.photo = ImageTk.PhotoImage(image)
        self.video_label.configure(image=self.photo, text="")

    def _stream_finished(self):
        if self.stop_event.is_set():
            return
        self.is_playing = False
        self.status_var.set("Stopped")
        self.video_label.configure(text="RTSP", image="")
        self.photo = None

    def stop(self):
        self.stop_event.set()
        if self.process is not None:
            try:
                self.process.terminate()
                self.process.wait(timeout=1.5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
        self.process = None
        self.is_playing = False
        self.status_var.set("Stopped")
        self.video_label.configure(text="RTSP", image="")
        self.photo = None

    def open_external(self):
        url = self.url_var.get().strip()
        if shutil.which("ffplay") is None:
            self.log(f"{self.title}: ffplay not found. URL: {url}")
            return
        try:
            subprocess.Popen(
                ["ffplay", "-rtsp_transport", "tcp", "-fflags", "nobuffer", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.log(f"{self.title}: opened FFplay {url}")
        except FileNotFoundError:
            self.log(f"{self.title}: ffplay not found. URL: {url}")


class ZT30Dashboard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SIYI ZT30 Control Station")
        self.geometry("1320x840")
        self.minsize(1120, 720)
        self.client: ZT30UDPClient | None = None
        self.live_attitude = tk.BooleanVar(value=False)
        self._setup_style()
        self._build_vars()
        self._build_ui()
        self._connect_client()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_style(self):
        self.configure(background="#11161d")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=("Inter", 10))
        style.configure("Root.TFrame", background="#11161d")
        style.configure("Panel.TFrame", background="#18202a")
        style.configure("Strip.TFrame", background="#202a36")
        style.configure("TLabel", background="#18202a", foreground="#e7edf5")
        style.configure("Header.TLabel", background="#11161d", foreground="#f6f8fb", font=("Inter", 18, "bold"))
        style.configure("SubHeader.TLabel", background="#11161d", foreground="#9da9b7")
        style.configure("PanelTitle.TLabel", background="#18202a", foreground="#f6f8fb", font=("Inter", 11, "bold"))
        style.configure("Muted.TLabel", background="#18202a", foreground="#8fa0b2")
        style.configure("Metric.TLabel", background="#202a36", foreground="#ffffff", font=("Inter", 16, "bold"))
        style.configure("MetricName.TLabel", background="#202a36", foreground="#9da9b7")
        style.configure("StreamPlaceholder.TLabel", background="#05070a", foreground="#526071", font=("Inter", 28, "bold"))
        style.configure("TButton", background="#263241", foreground="#f3f6fa", borderwidth=0, padding=(10, 7))
        style.map("TButton", background=[("active", "#334256"), ("disabled", "#222a34")])
        style.configure("Accent.TButton", background="#1f8f7a", foreground="#ffffff")
        style.map("Accent.TButton", background=[("active", "#25a78f")])
        style.configure("Danger.TButton", background="#a64545", foreground="#ffffff")
        style.map("Danger.TButton", background=[("active", "#bf5555")])
        style.configure("TEntry", fieldbackground="#0f141b", foreground="#f3f6fa", bordercolor="#344254")
        style.configure("TCombobox", fieldbackground="#0f141b", foreground="#f3f6fa", arrowcolor="#f3f6fa")
        style.configure("Horizontal.TScale", background="#18202a", troughcolor="#0f141b")
        style.configure("TCheckbutton", background="#18202a", foreground="#e7edf5")
        style.configure("TNotebook", background="#11161d", borderwidth=0)
        style.configure("TNotebook.Tab", background="#202a36", foreground="#d6dde6", padding=(12, 8))
        style.map("TNotebook.Tab", background=[("selected", "#1f8f7a")], foreground=[("selected", "#ffffff")])

    def _build_vars(self):
        self.ip_var = tk.StringVar(value=DEFAULT_IP)
        self.port_var = tk.IntVar(value=DEFAULT_PORT)
        self.main_url_var = tk.StringVar(value=rtsp_url(DEFAULT_IP, 1))
        self.sub_url_var = tk.StringVar(value=rtsp_url(DEFAULT_IP, 2))
        self.speed_var = tk.IntVar(value=35)
        self.yaw_angle_var = tk.DoubleVar(value=0.0)
        self.pitch_angle_var = tk.DoubleVar(value=0.0)
        self.zoom_var = tk.DoubleVar(value=4.5)
        self.image_mode_var = tk.StringVar(value=IMAGE_MODES[3])
        self.palette_var = tk.StringVar(value=THERMAL_PALETTES[0])
        self.temp_x_var = tk.IntVar(value=320)
        self.temp_y_var = tk.IntVar(value=256)
        self.metrics = {
            "yaw": tk.StringVar(value="-"),
            "pitch": tk.StringVar(value="-"),
            "roll": tk.StringVar(value="-"),
            "zoom": tk.StringVar(value="-"),
            "laser": tk.StringVar(value="-"),
            "mode": tk.StringVar(value="-"),
        }

    def _build_ui(self):
        root = ttk.Frame(self, style="Root.TFrame", padding=14)
        root.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(root, style="Root.TFrame")
        header.pack(fill=tk.X, pady=(0, 12))
        title = ttk.Frame(header, style="Root.TFrame")
        title.pack(side=tk.LEFT)
        ttk.Label(title, text="SIYI ZT30 Control Station", style="Header.TLabel").pack(anchor=tk.W)
        ttk.Label(title, text="UDP control, dual RTSP monitor, laser, thermal, and camera tools", style="SubHeader.TLabel").pack(anchor=tk.W)

        conn = ttk.Frame(header, style="Root.TFrame")
        conn.pack(side=tk.RIGHT)
        ttk.Label(conn, text="IP", style="SubHeader.TLabel").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Entry(conn, textvariable=self.ip_var, width=16).pack(side=tk.LEFT)
        ttk.Label(conn, text="Port", style="SubHeader.TLabel").pack(side=tk.LEFT, padx=(10, 4))
        ttk.Entry(conn, textvariable=self.port_var, width=7).pack(side=tk.LEFT)
        ttk.Button(conn, text="Reconnect", command=self._connect_client, style="Accent.TButton").pack(side=tk.LEFT, padx=(10, 0))

        body = ttk.Frame(root, style="Root.TFrame")
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=0, minsize=345)
        body.columnconfigure(1, weight=1)
        body.columnconfigure(2, weight=0, minsize=330)
        body.rowconfigure(0, weight=1)

        controls = ttk.Frame(body, style="Root.TFrame")
        controls.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        video = ttk.Frame(body, style="Root.TFrame")
        video.grid(row=0, column=1, sticky="nsew")
        side = ttk.Frame(body, style="Root.TFrame")
        side.grid(row=0, column=2, sticky="nsew", padx=(12, 0))

        self._build_control_stack(controls)
        self._build_video_stack(video)
        self._build_status_stack(side)

    def _panel(self, parent, title):
        frame = ttk.Frame(parent, style="Panel.TFrame", padding=10)
        frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(frame, text=title, style="PanelTitle.TLabel").pack(anchor=tk.W, pady=(0, 8))
        return frame

    def _build_control_stack(self, parent):
        gimbal = self._panel(parent, "Gimbal")
        speed = ttk.Frame(gimbal, style="Panel.TFrame")
        speed.pack(fill=tk.X)
        ttk.Label(speed, text="Speed").pack(side=tk.LEFT)
        ttk.Scale(speed, from_=5, to=100, variable=self.speed_var, orient=tk.HORIZONTAL).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)
        ttk.Label(speed, textvariable=self.speed_var, style="Muted.TLabel", width=4).pack(side=tk.RIGHT)

        pad = ttk.Frame(gimbal, style="Panel.TFrame")
        pad.pack(pady=10)
        self._hold_button(pad, "Up", 0, 1, 0, 1)
        self._hold_button(pad, "Left", 1, 0, -1, 0)
        ttk.Button(pad, text="Center", command=lambda: self._run("center", self.client.center), style="Accent.TButton").grid(row=1, column=1, padx=4, pady=4, ipadx=10)
        self._hold_button(pad, "Right", 1, 2, 1, 0)
        self._hold_button(pad, "Down", 2, 1, 0, -1)

        angle = ttk.Frame(gimbal, style="Panel.TFrame")
        angle.pack(fill=tk.X, pady=(2, 0))
        ttk.Entry(angle, textvariable=self.yaw_angle_var, width=8).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Entry(angle, textvariable=self.pitch_angle_var, width=8).pack(side=tk.LEFT, padx=6)
        ttk.Button(angle, text="Set Angle", command=lambda: self._run("set angle", lambda: self.client.set_angle(self.yaw_angle_var.get(), self.pitch_angle_var.get()))).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        modes = ttk.Frame(gimbal, style="Panel.TFrame")
        modes.pack(fill=tk.X, pady=(10, 0))
        for mode in ("lock", "follow", "fpv"):
            ttk.Button(modes, text=mode.upper(), command=lambda value=mode: self._run(f"mode {value}", lambda: self.client.set_motion_mode(value))).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=3)

        camera = self._panel(parent, "Camera")
        row = ttk.Frame(camera, style="Panel.TFrame")
        row.pack(fill=tk.X)
        ttk.Button(row, text="Photo", command=lambda: self._run("photo", self.client.take_photo), style="Accent.TButton").pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
        ttk.Button(row, text="Record", command=lambda: self._run("record", self.client.toggle_record)).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=4)
        ttk.Button(row, text="AF", command=lambda: self._run("auto focus", self.client.auto_focus)).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0))

        zoom = ttk.Frame(camera, style="Panel.TFrame")
        zoom.pack(fill=tk.X, pady=(10, 0))
        self._press_button(zoom, "Zoom -", lambda: self.client.zoom_out(), lambda: self.client.zoom_stop()).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
        ttk.Button(zoom, text="Stop", command=lambda: self._run("zoom stop", self.client.zoom_stop)).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=4)
        self._press_button(zoom, "Zoom +", lambda: self.client.zoom_in(), lambda: self.client.zoom_stop()).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0))

        abs_zoom = ttk.Frame(camera, style="Panel.TFrame")
        abs_zoom.pack(fill=tk.X, pady=(10, 0))
        ttk.Entry(abs_zoom, textvariable=self.zoom_var, width=8).pack(side=tk.LEFT)
        ttk.Button(abs_zoom, text="Set Zoom", command=lambda: self._run("absolute zoom", lambda: self.client.absolute_zoom(self.zoom_var.get()))).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        ttk.Button(abs_zoom, text="Get", command=self._refresh_zoom).pack(side=tk.RIGHT)

        focus = ttk.Frame(camera, style="Panel.TFrame")
        focus.pack(fill=tk.X, pady=(10, 0))
        self._press_button(focus, "Near", lambda: self.client.focus_near(), lambda: self.client.focus_stop()).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
        ttk.Button(focus, text="Stop", command=lambda: self._run("focus stop", self.client.focus_stop)).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=4)
        self._press_button(focus, "Far", lambda: self.client.focus_far(), lambda: self.client.focus_stop()).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0))

        image = self._panel(parent, "Image Mode")
        ttk.Combobox(image, textvariable=self.image_mode_var, values=list(IMAGE_MODES.values()), state="readonly").pack(fill=tk.X)
        ttk.Button(image, text="Apply Image Mode", command=lambda: self._run("image mode", lambda: self.client.set_image_mode(self.image_mode_var.get()))).pack(fill=tk.X, pady=(8, 0))

        thermal = self._panel(parent, "Thermal")
        ttk.Combobox(thermal, textvariable=self.palette_var, values=list(THERMAL_PALETTES.values()), state="readonly").pack(fill=tk.X)
        trow = ttk.Frame(thermal, style="Panel.TFrame")
        trow.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(trow, text="Palette", command=lambda: self._run("palette", lambda: self.client.set_thermal_palette(self.palette_var.get()))).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
        ttk.Button(trow, text="Gain H", command=lambda: self._run("gain high", lambda: self.client.set_thermal_gain(True))).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=4)
        ttk.Button(trow, text="Gain L", command=lambda: self._run("gain low", lambda: self.client.set_thermal_gain(False))).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0))
        point = ttk.Frame(thermal, style="Panel.TFrame")
        point.pack(fill=tk.X, pady=(8, 0))
        ttk.Entry(point, textvariable=self.temp_x_var, width=7).pack(side=tk.LEFT)
        ttk.Entry(point, textvariable=self.temp_y_var, width=7).pack(side=tk.LEFT, padx=6)
        ttk.Button(point, text="Point Temp", command=lambda: self._run("point temp", lambda: self.client.request_temperature_point(self.temp_x_var.get(), self.temp_y_var.get()))).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(thermal, text="Full Image Temperature", command=lambda: self._run("full temp", self.client.request_temperature_full_image)).pack(fill=tk.X, pady=(8, 0))

    def _build_video_stack(self, parent):
        parent.rowconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)
        self.main_stream = StreamPanel(parent, "Main Stream", self.main_url_var, self._log)
        self.main_stream.grid(row=0, column=0, sticky="nsew", pady=(0, 6))
        self.sub_stream = StreamPanel(parent, "Sub Stream", self.sub_url_var, self._log)
        self.sub_stream.grid(row=1, column=0, sticky="nsew", pady=(6, 0))

    def _build_status_stack(self, parent):
        metrics = ttk.Frame(parent, style="Panel.TFrame", padding=10)
        metrics.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(metrics, text="Telemetry", style="PanelTitle.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self._metric(metrics, "Yaw", "yaw", 1, 0)
        self._metric(metrics, "Pitch", "pitch", 1, 1)
        self._metric(metrics, "Roll", "roll", 2, 0)
        self._metric(metrics, "Zoom", "zoom", 2, 1)
        self._metric(metrics, "Laser", "laser", 3, 0)
        self._metric(metrics, "Mode", "mode", 3, 1)
        metrics.columnconfigure(0, weight=1)
        metrics.columnconfigure(1, weight=1)

        actions = ttk.Frame(metrics, style="Panel.TFrame")
        actions.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="Refresh", command=self._refresh_all, style="Accent.TButton").pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
        ttk.Checkbutton(actions, text="Live", variable=self.live_attitude, command=self._toggle_live_attitude).pack(side=tk.LEFT, padx=8)

        laser = ttk.Frame(parent, style="Panel.TFrame", padding=10)
        laser.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(laser, text="Laser Rangefinder", style="PanelTitle.TLabel").pack(anchor=tk.W, pady=(0, 8))
        lrow = ttk.Frame(laser, style="Panel.TFrame")
        lrow.pack(fill=tk.X)
        ttk.Button(lrow, text="ON", command=lambda: self._run("laser on", lambda: self.client.set_laser(True))).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
        ttk.Button(lrow, text="OFF", command=lambda: self._run("laser off", lambda: self.client.set_laser(False))).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=4)
        ttk.Button(lrow, text="Range", command=self._refresh_laser_range).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0))
        ttk.Button(laser, text="Target Lat/Lon", command=lambda: self._run("laser latlon", self.client.request_laser_target_latlon)).pack(fill=tk.X, pady=(8, 0))

        maint = ttk.Frame(parent, style="Panel.TFrame", padding=10)
        maint.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(maint, text="Maintenance", style="PanelTitle.TLabel").pack(anchor=tk.W, pady=(0, 8))
        ttk.Button(maint, text="Set UTC Time", command=lambda: self._run("utc", self.client.set_utc_time)).pack(fill=tk.X)
        ttk.Button(maint, text="Restart Camera", command=lambda: self._confirm("Restart camera?", "restart camera", lambda: self.client.soft_restart(camera=True))).pack(fill=tk.X, pady=(8, 0))
        ttk.Button(maint, text="Restart Gimbal", command=lambda: self._confirm("Restart gimbal?", "restart gimbal", lambda: self.client.soft_restart(gimbal=True))).pack(fill=tk.X, pady=(8, 0))
        ttk.Button(maint, text="Format SD Card", command=lambda: self._confirm("Format SD card?", "format sd", self.client.format_sd_card), style="Danger.TButton").pack(fill=tk.X, pady=(8, 0))

        log_panel = ttk.Frame(parent, style="Panel.TFrame", padding=10)
        log_panel.pack(fill=tk.BOTH, expand=True)
        ttk.Label(log_panel, text="Log", style="PanelTitle.TLabel").pack(anchor=tk.W, pady=(0, 8))
        self.log_text = scrolledtext.ScrolledText(
            log_panel,
            height=12,
            bg="#0f141b",
            fg="#dce5ee",
            insertbackground="#dce5ee",
            relief=tk.FLAT,
            wrap=tk.WORD,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _metric(self, parent, label, key, row, col):
        frame = ttk.Frame(parent, style="Strip.TFrame", padding=9)
        frame.grid(row=row, column=col, sticky="ew", padx=4, pady=4)
        ttk.Label(frame, text=label, style="MetricName.TLabel").pack(anchor=tk.W)
        ttk.Label(frame, textvariable=self.metrics[key], style="Metric.TLabel").pack(anchor=tk.W)

    def _hold_button(self, parent, text, row, col, yaw, pitch):
        btn = ttk.Button(parent, text=text)
        btn.grid(row=row, column=col, padx=4, pady=4, ipadx=12)
        btn.bind("<ButtonPress-1>", lambda _event: self._run("rotate", lambda: self.client.rotate_speed(yaw * self.speed_var.get(), pitch * self.speed_var.get())))
        btn.bind("<ButtonRelease-1>", lambda _event: self._run("stop", self.client.stop_rotation))
        return btn

    def _press_button(self, parent, text, start, stop):
        btn = ttk.Button(parent, text=text)
        btn.bind("<ButtonPress-1>", lambda _event: self._run(text.lower(), start))
        btn.bind("<ButtonRelease-1>", lambda _event: self._run(f"{text.lower()} stop", stop))
        return btn

    def _connect_client(self):
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
        host = self.ip_var.get().strip()
        self.client = ZT30UDPClient(host, int(self.port_var.get()))
        self.main_url_var.set(rtsp_url(host, 1))
        self.sub_url_var.set(rtsp_url(host, 2))
        self._log(f"Connected target {host}:{self.port_var.get()}")

    def _run(self, label, func, on_result=None):
        if self.client is None:
            self._log(f"{label}: no client")
            return

        def worker():
            try:
                result = func()
                self.after(0, lambda: self._handle_result(label, result, on_result))
            except Exception as exc:
                self.after(0, lambda: self._log(f"{label}: ERROR {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_result(self, label, result, on_result):
        if on_result:
            on_result(result)
        self._log(f"{label}: {self._format_result(result)}")

    def _format_result(self, result):
        if result is None:
            return "OK / no response"
        try:
            return json.dumps(result, indent=2, default=str)
        except TypeError:
            return str(result)

    def _refresh_all(self):
        self._run("attitude", self.client.request_attitude, self._update_attitude)
        self._run("zoom", self.client.request_zoom, lambda value: self.metrics["zoom"].set("-" if value is None else f"{value:.1f}x"))
        self._run("laser status", self.client.request_laser_status, lambda value: self.metrics["laser"].set("ON" if value else "OFF" if value is False else "-"))
        self._run("mode", self.client.request_working_mode, lambda value: self.metrics["mode"].set(value or "-"))

    def _refresh_zoom(self):
        self._run("zoom", self.client.request_zoom, lambda value: self.metrics["zoom"].set("-" if value is None else f"{value:.1f}x"))

    def _refresh_laser_range(self):
        self._run("laser range", self.client.request_laser_range, lambda value: self.metrics["laser"].set("-" if value is None else f"{value:.1f} m"))

    def _update_attitude(self, data):
        if not data:
            return
        self.metrics["yaw"].set(f"{data['yaw_deg']:.1f}")
        self.metrics["pitch"].set(f"{data['pitch_deg']:.1f}")
        self.metrics["roll"].set(f"{data['roll_deg']:.1f}")

    def _toggle_live_attitude(self):
        if self.live_attitude.get():
            self._poll_attitude()

    def _poll_attitude(self):
        if not self.live_attitude.get():
            return
        self._run("attitude", self.client.request_attitude, self._update_attitude)
        self.after(1000, self._poll_attitude)

    def _confirm(self, prompt, label, func):
        if messagebox.askyesno("Confirm", prompt):
            self._run(label, func)

    def _log(self, text):
        if not hasattr(self, "log_text"):
            return
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)

    def _on_close(self):
        self.live_attitude.set(False)
        self.main_stream.stop()
        self.sub_stream.stop()
        if self.client is not None:
            self.client.close()
        self.destroy()


if __name__ == "__main__":
    app = ZT30Dashboard()
    app.mainloop()
