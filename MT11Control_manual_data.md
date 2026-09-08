# Data Draft Manual Aplikasi MT11Control

Dokumen ini berisi data utama untuk manual aplikasi MT11Control.

## Identitas

- Nama aplikasi: MT11Control
- Jenis aplikasi: SDK Python dan antarmuka desktop untuk kontrol gimbal kamera UniPod MT11
- Perangkat utama: Reebot/UniPod MT11 gimbal camera
- Protokol komunikasi: UniPod MT11 External SDK
- Transport kontrol: UDP
- IP default kamera: 192.168.144.25
- Port SDK: 37260
- RTSP utama: rtsp://192.168.144.25:8554/video1
- RTSP kedua: rtsp://192.168.144.25:8554/video2
- Web media: http://192.168.144.25:82

## Fitur Utama

- Monitoring RTSP video1 dan video2
- Kontrol gimbal yaw/pitch, center, dan angle
- Zoom, focus, foto, dan rekam
- Pilihan view MT11: Zoom + Thermal, Thermal + Zoom, Zoom/Thermal + Thermal
- Thermal palette dan pengukuran suhu titik/area/full-frame
- Laser rangefinder dan koordinat target
- AI tracking built-in MT11 dengan click-to-track, box selection, cancel, dan overlay tracking
- Media browser untuk file foto/video dari TF card
- Joystick `/dev/input/js0`
- Maintenance: UTC time, TF card info, SD format, reboot

## Instalasi

```bash
./setup.sh
./run_dashboard.sh
```

Jalankan manual:

```bash
python3 ui/mt11_dashboard_qt.py
```

Build AppImage:

```bash
./install.sh
./MT11Control.AppImage
```

## Jaringan

Atur komputer/companion computer dalam subnet yang sama dengan MT11.

```text
IP address : 192.168.144.30
Netmask    : 255.255.255.0
Gateway    : kosong atau 192.168.144.1
```

Tes koneksi:

```bash
ping 192.168.144.25
```

## Catatan Teknis

- AI tracking MT11 tidak memakai modul/IP terpisah; command AI dikirim ke IP kamera utama.
- Koordinat AI overlay memakai basis 1280 x 720.
- Koordinat thermal memakai basis 640 x 512.
- Web API media memakai endpoint `/api/v1/...` langsung dari port 82.
- Folder package Python bernama `mt11_sdk`; class utama tersedia sebagai `MT11UDPClient`, `MT11AITrackingClient`, dan `MT11WebClient`.
