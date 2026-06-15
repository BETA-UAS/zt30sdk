# SIYI ZT30 UDP SDK and Control UI

This package is an independent Python SDK and desktop UI for the SIYI ZT30 gimbal camera using the SIYI UDP protocol.

Default device settings:

```text
ZT30 IP      : 192.168.144.25
SDK UDP port : 37260
RTSP main    : rtsp://192.168.144.25:8554/video1
RTSP sub     : rtsp://192.168.144.25:8554/video2
Web media    : http://192.168.144.25:82//cgi-bin/media.cgi
```

## Features implemented

### UDP SDK

Implemented command groups:

- Firmware version and hardware ID
- Working mode and configuration status
- Gimbal speed rotation
- Gimbal center
- Gimbal absolute angle control
- Gimbal attitude request
- Manual zoom
- Absolute zoom
- Zoom value and max zoom request
- Manual focus
- Auto focus
- Photo
- Record toggle
- Lock, follow, and FPV motion mode
- Codec request and configuration
- Image mode request and configuration
- Thermal point temperature
- Thermal box temperature
- Thermal full image temperature
- Thermal palette request and configuration
- Thermal RAW mode
- Thermal gain request and configuration
- Thermal calibration request and configuration
- Thermal calibration parameter request and configuration
- Laser range request
- Laser target latitude and longitude request
- Laser status request and laser ON/OFF
- Flight controller attitude injection
- Flight controller GPS injection
- Gimbal data stream request
- UTC time setting
- SD card formatting
- Camera and gimbal soft restart
- Raw hex sending for debugging

### Web media helper

Implemented HTTP helpers:

- Get media directories
- Get media count
- Get media list

### UI

The included Tkinter dashboard provides:

- Connection setting
- Main stream and sub stream panels
- Gimbal yaw and pitch movement buttons
- Center command
- Angle control
- Live attitude telemetry
- Zoom and focus controls
- Photo and record buttons
- Image mode selector
- Laser rangefinder controls
- Thermal palette and gain controls
- Full image and point thermometric request
- Maintenance buttons
- Embedded RTSP playback through `ffmpeg`, with optional external `ffplay`

## Requirements

Python 3.9 or newer is recommended.

The SDK uses only Python standard library.
The UI uses Tkinter, which is included in most Python installations. On Ubuntu or Debian, install it with:

```bash
sudo apt install python3-tk
```

To decode RTSP streams in the dashboard, install FFmpeg:

```bash
sudo apt install ffmpeg
```

The dashboard uses Pillow to display decoded FFmpeg frames in Tkinter:

```bash
python3 -m pip install Pillow
```

If `ffplay` is available from the FFmpeg package, the dashboard can also open a stream in an external FFplay window.


## Network setup

Set your computer or companion computer Ethernet IP to the same subnet as ZT30.

Example:

```text
IP address : 192.168.144.30
Netmask    : 255.255.255.0
Gateway    : empty or 192.168.144.1
```

Test connection:

```bash
ping 192.168.144.25
```

## Run the example

From the package root:

```bash
python3 examples/basic_control.py
```

## Run the UI

From the package root:

```bash
python3 ui/zt30_dashboard_ui.py
```

The original compact UI is still available:

```bash
python3 ui/zt30_control_ui.py
```

## Basic SDK usage

```python
from siyi_zt30 import ZT30UDPClient

cam = ZT30UDPClient("192.168.144.25")

print(cam.request_firmware_version())
print(cam.center())
print(cam.request_attitude())

cam.rotate_speed(yaw=30, pitch=0)
cam.stop_rotation()

cam.absolute_zoom(4.5)
cam.take_photo()
cam.toggle_record()

cam.set_laser(True)
print(cam.request_laser_range())

cam.close()
```

## Important operation notes

For speed movement, always send stop after releasing a button or joystick:

```python
cam.rotate_speed(40, 0)
cam.stop_rotation()
```

The laser rangefinder is off by default on newer firmware. Turn it on before requesting range:

```python
cam.set_laser(True)
print(cam.request_laser_range())
```

Do not use laser ranging indoors at less than 5 m, especially toward reflective targets.

## File layout

```text
siyi_zt30/
  __init__.py
  client.py
  constants.py
  protocol.py
  web.py
examples/
  basic_control.py
ui/
  zt30_control_ui.py
README.md
pyproject.toml
```
