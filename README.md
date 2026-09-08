# UniPod MT11 SDK and MT11Control

Python SDK and PyQt5 desktop control UI for the Reebot/UniPod MT11 gimbal camera.

Default device settings:

```text
MT11 IP       : 192.168.144.25
SDK UDP port  : 37260
RTSP main     : rtsp://192.168.144.25:8554/video1
RTSP sub      : rtsp://192.168.144.25:8554/video2
Web media     : http://192.168.144.25:82
```

## Implemented Features

- Firmware and hardware ID requests
- Gimbal speed, center, angle, and attitude controls
- Zoom, focus, photo, record, and motion mode controls
- MT11 video stitching modes
- Thermal point, box, full-frame, palette, and gain controls
- Laser enable, range, and target coordinate requests
- Codec request and configuration
- UTC time, system time, TF card info, SD format, and reboot commands
- MT11 built-in AI tracking and tracking box stream
- MT11 media web API browsing and downloads
- PyQt5 live RTSP dashboard with joystick support

## Setup

```bash
./setup.sh
./run_mt11control.sh
```

Manual run:

```bash
python3 ui/mt11_dashboard_qt.py
```

Build AppImage:

```bash
./install.sh
./MT11Control.AppImage
```

## Network

Set your computer or companion computer Ethernet IP to the same subnet as MT11.

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

## Basic SDK Usage

```python
from mt11_sdk import MT11UDPClient

cam = MT11UDPClient("192.168.144.25")

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

## AI Tracking

MT11 AI tracking is built into the camera and uses the same IP/UDP port as the main SDK endpoint.

```python
from mt11_sdk import DEFAULT_AI_IP, MT11AITrackingClient

ai = MT11AITrackingClient(DEFAULT_AI_IP)

print(ai.request_firmware_version())
print(ai.set_recognition_enabled(True))
print(ai.track_point(640, 360))
print(ai.set_coordinate_stream_enabled(True))

ai.start_coordinate_listener(lambda box: print(box))
```

The dashboard normalizes live-view clicks to the MT11 AI coordinate space: `1280 x 720`.

## Notes

- MT11 video stitching uses two stream IDs, not the old one-byte mode enum.
- Supported MT11 view presets in the dashboard are Zoom + Thermal, Thermal + Zoom, and Zoom/Thermal + Thermal.
- The package directory is still named `mt11_sdk` to keep existing imports stable, but the exported primary classes are `MT11UDPClient`, `MT11AITrackingClient`, and `MT11WebClient`.
