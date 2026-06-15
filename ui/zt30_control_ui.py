#!/usr/bin/env python3
"""Tkinter UI for SIYI ZT30 UDP control."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

# Allow running from source tree without installation.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from siyi_zt30 import ZT30UDPClient, DEFAULT_IP, DEFAULT_PORT, DEFAULT_RTSP_MAIN, DEFAULT_RTSP_SUB
from siyi_zt30.constants import IMAGE_MODES, THERMAL_PALETTES


class ZT30ControlUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("ZT30 UDP Control")
        self.geometry("980x650")
        self.minsize(900, 600)
        self.client = None
        self._build_ui()
        self._connect_client()

    def _build_ui(self):
        root = ttk.Frame(self, padding=10)
        root.pack(fill=tk.BOTH, expand=True)

        conn = ttk.LabelFrame(root, text="Connection")
        conn.pack(fill=tk.X)
        ttk.Label(conn, text="IP").pack(side=tk.LEFT, padx=(8, 2))
        self.ip_var = tk.StringVar(value=DEFAULT_IP)
        ttk.Entry(conn, textvariable=self.ip_var, width=18).pack(side=tk.LEFT)
        ttk.Label(conn, text="Port").pack(side=tk.LEFT, padx=(8, 2))
        self.port_var = tk.IntVar(value=DEFAULT_PORT)
        ttk.Entry(conn, textvariable=self.port_var, width=8).pack(side=tk.LEFT)
        ttk.Button(conn, text="Reconnect", command=self._connect_client).pack(side=tk.LEFT, padx=8)
        ttk.Button(conn, text="Request Firmware", command=lambda: self._run("firmware", self.client.request_firmware_version)).pack(side=tk.LEFT, padx=4)
        ttk.Button(conn, text="Request Status", command=lambda: self._run("status", self.client.request_config)).pack(side=tk.LEFT, padx=4)

        body = ttk.Frame(root)
        body.pack(fill=tk.BOTH, expand=True, pady=10)

        left = ttk.Frame(body)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=False)
        mid = ttk.Frame(body)
        mid.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10)
        right = ttk.Frame(body)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_gimbal_panel(left)
        self._build_camera_panel(mid)
        self._build_thermal_laser_panel(right)
        self._build_log(root)

    def _build_gimbal_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="Gimbal")
        frame.pack(fill=tk.X, pady=(0, 10))

        speed_frame = ttk.Frame(frame)
        speed_frame.pack(fill=tk.X, padx=8, pady=8)
        ttk.Label(speed_frame, text="Speed").pack(side=tk.LEFT)
        self.speed_var = tk.IntVar(value=35)
        ttk.Scale(speed_frame, from_=5, to=100, variable=self.speed_var, orient=tk.HORIZONTAL, length=160).pack(side=tk.LEFT, padx=8)

        grid = ttk.Frame(frame)
        grid.pack(padx=8, pady=8)

        def hold_button(text, row, col, yaw, pitch):
            btn = ttk.Button(grid, text=text, width=10)
            btn.grid(row=row, column=col, padx=4, pady=4)
            btn.bind("<ButtonPress-1>", lambda _e: self._run("rotate", lambda: self.client.rotate_speed(yaw * self.speed_var.get(), pitch * self.speed_var.get())))
            btn.bind("<ButtonRelease-1>", lambda _e: self._run("stop", self.client.stop_rotation))
            return btn

        hold_button("Pitch Up", 0, 1, 0, 1)
        hold_button("Yaw Left", 1, 0, -1, 0)
        ttk.Button(grid, text="Center", width=10, command=lambda: self._run("center", self.client.center)).grid(row=1, column=1, padx=4, pady=4)
        hold_button("Yaw Right", 1, 2, 1, 0)
        hold_button("Pitch Down", 2, 1, 0, -1)

        angle = ttk.LabelFrame(parent, text="Angle Control")
        angle.pack(fill=tk.X, pady=(0, 10))
        self.yaw_angle_var = tk.DoubleVar(value=0.0)
        self.pitch_angle_var = tk.DoubleVar(value=0.0)
        row1 = ttk.Frame(angle)
        row1.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(row1, text="Yaw deg").pack(side=tk.LEFT)
        ttk.Entry(row1, textvariable=self.yaw_angle_var, width=8).pack(side=tk.LEFT, padx=4)
        ttk.Label(row1, text="Pitch deg").pack(side=tk.LEFT)
        ttk.Entry(row1, textvariable=self.pitch_angle_var, width=8).pack(side=tk.LEFT, padx=4)
        ttk.Button(angle, text="Set Angle", command=lambda: self._run("set angle", lambda: self.client.set_angle(self.yaw_angle_var.get(), self.pitch_angle_var.get()))).pack(padx=8, pady=4, fill=tk.X)
        ttk.Button(angle, text="Request Attitude", command=lambda: self._run("attitude", self.client.request_attitude)).pack(padx=8, pady=4, fill=tk.X)

        mode = ttk.LabelFrame(parent, text="Motion Mode")
        mode.pack(fill=tk.X)
        for m in ("lock", "follow", "fpv"):
            ttk.Button(mode, text=m.upper(), command=lambda mode=m: self._run(f"mode {mode}", lambda: self.client.set_motion_mode(mode))).pack(side=tk.LEFT, padx=5, pady=8)

    def _build_camera_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="Camera and Zoom")
        frame.pack(fill=tk.X, pady=(0, 10))

        row = ttk.Frame(frame)
        row.pack(fill=tk.X, padx=8, pady=6)
        for text, cmd in [
            ("Zoom In", self.client_placeholder("zoom_in")),
            ("Zoom Stop", self.client_placeholder("zoom_stop")),
            ("Zoom Out", self.client_placeholder("zoom_out")),
            ("AF", self.client_placeholder("auto_focus")),
        ]:
            ttk.Button(row, text=text, command=cmd).pack(side=tk.LEFT, padx=4)

        row2 = ttk.Frame(frame)
        row2.pack(fill=tk.X, padx=8, pady=6)
        self.abs_zoom_var = tk.DoubleVar(value=4.5)
        ttk.Label(row2, text="Absolute Zoom").pack(side=tk.LEFT)
        ttk.Entry(row2, textvariable=self.abs_zoom_var, width=8).pack(side=tk.LEFT, padx=4)
        ttk.Button(row2, text="Set", command=lambda: self._run("absolute zoom", lambda: self.client.absolute_zoom(self.abs_zoom_var.get()))).pack(side=tk.LEFT, padx=4)
        ttk.Button(row2, text="Get Zoom", command=lambda: self._run("zoom", self.client.request_zoom)).pack(side=tk.LEFT, padx=4)
        ttk.Button(row2, text="Max Zoom", command=lambda: self._run("max zoom", self.client.request_max_zoom)).pack(side=tk.LEFT, padx=4)

        row3 = ttk.Frame(frame)
        row3.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(row3, text="Focus Near", command=lambda: self._run("focus near", self.client.focus_near)).pack(side=tk.LEFT, padx=4)
        ttk.Button(row3, text="Focus Stop", command=lambda: self._run("focus stop", self.client.focus_stop)).pack(side=tk.LEFT, padx=4)
        ttk.Button(row3, text="Focus Far", command=lambda: self._run("focus far", self.client.focus_far)).pack(side=tk.LEFT, padx=4)

        row4 = ttk.Frame(frame)
        row4.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(row4, text="Take Photo", command=lambda: self._run("photo", self.client.take_photo)).pack(side=tk.LEFT, padx=4)
        ttk.Button(row4, text="Toggle Record", command=lambda: self._run("record", self.client.toggle_record)).pack(side=tk.LEFT, padx=4)
        ttk.Button(row4, text="Set UTC", command=lambda: self._run("utc", self.client.set_utc_time)).pack(side=tk.LEFT, padx=4)

        im = ttk.LabelFrame(parent, text="Image Mode")
        im.pack(fill=tk.X, pady=(0, 10))
        self.image_mode_var = tk.StringVar(value=IMAGE_MODES[3])
        ttk.Combobox(im, textvariable=self.image_mode_var, values=list(IMAGE_MODES.values()), state="readonly", width=38).pack(padx=8, pady=6, fill=tk.X)
        ttk.Button(im, text="Set Image Mode", command=lambda: self._run("image mode", lambda: self.client.set_image_mode(self.image_mode_var.get()))).pack(padx=8, pady=4, fill=tk.X)
        ttk.Button(im, text="Request Image Mode", command=lambda: self._run("request image mode", self.client.request_image_mode)).pack(padx=8, pady=4, fill=tk.X)

        rtsp = ttk.LabelFrame(parent, text="RTSP")
        rtsp.pack(fill=tk.X)
        self.rtsp_main_var = tk.StringVar(value=DEFAULT_RTSP_MAIN)
        self.rtsp_sub_var = tk.StringVar(value=DEFAULT_RTSP_SUB)
        ttk.Entry(rtsp, textvariable=self.rtsp_main_var).pack(padx=8, pady=4, fill=tk.X)
        ttk.Entry(rtsp, textvariable=self.rtsp_sub_var).pack(padx=8, pady=4, fill=tk.X)
        ttk.Button(rtsp, text="Open Main Stream with FFplay", command=lambda: self._open_stream(self.rtsp_main_var.get())).pack(padx=8, pady=4, fill=tk.X)
        ttk.Button(rtsp, text="Open Sub Stream with FFplay", command=lambda: self._open_stream(self.rtsp_sub_var.get())).pack(padx=8, pady=4, fill=tk.X)

    def _build_thermal_laser_panel(self, parent):
        laser = ttk.LabelFrame(parent, text="Laser Rangefinder")
        laser.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(laser, text="Laser ON", command=lambda: self._run("laser on", lambda: self.client.set_laser(True))).pack(side=tk.LEFT, padx=5, pady=8)
        ttk.Button(laser, text="Laser OFF", command=lambda: self._run("laser off", lambda: self.client.set_laser(False))).pack(side=tk.LEFT, padx=5, pady=8)
        ttk.Button(laser, text="Status", command=lambda: self._run("laser status", self.client.request_laser_status)).pack(side=tk.LEFT, padx=5, pady=8)
        ttk.Button(laser, text="Range", command=lambda: self._run("laser range", self.client.request_laser_range)).pack(side=tk.LEFT, padx=5, pady=8)
        ttk.Button(laser, text="Target Lat/Lon", command=lambda: self._run("laser latlon", self.client.request_laser_target_latlon)).pack(side=tk.LEFT, padx=5, pady=8)

        thermal = ttk.LabelFrame(parent, text="Thermal")
        thermal.pack(fill=tk.X, pady=(0, 10))
        self.palette_var = tk.StringVar(value=THERMAL_PALETTES[0])
        ttk.Combobox(thermal, textvariable=self.palette_var, values=list(THERMAL_PALETTES.values()), state="readonly").pack(padx=8, pady=6, fill=tk.X)
        ttk.Button(thermal, text="Set Palette", command=lambda: self._run("palette", lambda: self.client.set_thermal_palette(self.palette_var.get()))).pack(padx=8, pady=4, fill=tk.X)
        ttk.Button(thermal, text="Request Palette", command=lambda: self._run("request palette", self.client.request_thermal_palette)).pack(padx=8, pady=4, fill=tk.X)
        ttk.Button(thermal, text="Gain High", command=lambda: self._run("gain high", lambda: self.client.set_thermal_gain(True))).pack(padx=8, pady=4, fill=tk.X)
        ttk.Button(thermal, text="Gain Low", command=lambda: self._run("gain low", lambda: self.client.set_thermal_gain(False))).pack(padx=8, pady=4, fill=tk.X)
        ttk.Button(thermal, text="Full Image Temp", command=lambda: self._run("full temp", self.client.request_temperature_full_image)).pack(padx=8, pady=4, fill=tk.X)

        point = ttk.LabelFrame(parent, text="Point Temperature")
        point.pack(fill=tk.X, pady=(0, 10))
        self.tx_var = tk.IntVar(value=320)
        self.ty_var = tk.IntVar(value=256)
        row = ttk.Frame(point)
        row.pack(padx=8, pady=6)
        ttk.Label(row, text="X").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.tx_var, width=8).pack(side=tk.LEFT, padx=4)
        ttk.Label(row, text="Y").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.ty_var, width=8).pack(side=tk.LEFT, padx=4)
        ttk.Button(point, text="Measure Point", command=lambda: self._run("point temp", lambda: self.client.request_temperature_point(self.tx_var.get(), self.ty_var.get()))).pack(padx=8, pady=4, fill=tk.X)

        maint = ttk.LabelFrame(parent, text="Maintenance")
        maint.pack(fill=tk.X)
        ttk.Button(maint, text="Soft Restart Camera", command=lambda: self._confirm_run("Restart camera?", "restart camera", lambda: self.client.soft_restart(camera=True))).pack(padx=8, pady=4, fill=tk.X)
        ttk.Button(maint, text="Soft Restart Gimbal", command=lambda: self._confirm_run("Restart gimbal?", "restart gimbal", lambda: self.client.soft_restart(gimbal=True))).pack(padx=8, pady=4, fill=tk.X)
        ttk.Button(maint, text="Format SD Card", command=lambda: self._confirm_run("Format SD card?", "format sd", self.client.format_sd_card)).pack(padx=8, pady=4, fill=tk.X)

    def _build_log(self, parent):
        log_frame = ttk.LabelFrame(parent, text="Log")
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log = tk.Text(log_frame, height=10, wrap=tk.WORD)
        self.log.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    def client_placeholder(self, method_name):
        return lambda: self._run(method_name, getattr(self.client, method_name))

    def _connect_client(self):
        if self.client:
            self.client.close()
        self.client = ZT30UDPClient(self.ip_var.get().strip(), int(self.port_var.get()))
        self._log(f"Connected target {self.client.host}:{self.client.port}")

    def _run(self, label, func):
        def worker():
            try:
                result = func()
                self.after(0, lambda: self._log(f"{label}: {self._format_result(result)}"))
            except Exception as exc:
                self.after(0, lambda: self._log(f"{label}: ERROR {exc}"))
        threading.Thread(target=worker, daemon=True).start()

    def _confirm_run(self, prompt, label, func):
        if messagebox.askyesno("Confirm", prompt):
            self._run(label, func)

    def _format_result(self, result):
        if result is None:
            return "OK / no response"
        try:
            return json.dumps(result, indent=2, default=str)
        except TypeError:
            return str(result)

    def _log(self, text):
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)

    def _open_stream(self, url):
        if shutil.which("ffplay") is None:
            self._log(f"ffplay not found. RTSP URL: {url}")
            return
        try:
            subprocess.Popen(["ffplay", "-rtsp_transport", "tcp", "-fflags", "nobuffer", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._log(f"Opening FFplay: {url}")
        except FileNotFoundError:
            self._log("ffplay not found. Install ffmpeg or open the RTSP URL manually.")


if __name__ == "__main__":
    app = ZT30ControlUI()
    app.mainloop()
