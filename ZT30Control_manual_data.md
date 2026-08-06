# Data Draft Manual Aplikasi ZT30Control

Dokumen ini berisi data utama yang dapat digunakan untuk menyusun manual aplikasi ZT30Control.

## 1. Informasi Umum Aplikasi

- Nama aplikasi: ZT30Control
- Jenis aplikasi: SDK Python dan antarmuka desktop untuk kontrol gimbal kamera SIYI ZT30
- Fungsi utama:
  - Mengontrol gimbal (pan/tilt, center, mode lock/follow/FPV)
  - Mengontrol zoom dan fokus
  - Mengambil foto dan merekam video
  - Mengakses stream video RTSP
  - Mengelola media hasil tangkapan
  - Mengontrol AI Tracking Module II
  - Mengakses laser rangefinder dan data termal
- Platform: Linux
- Bahasa pemrograman: Python 3.9+
- UI framework: PyQt5

## 2. Spesifikasi Perangkat yang Didukung

- Perangkat utama: SIYI ZT30 gimbal camera
- Modul AI: SIYI AI Tracking Module II
- Protokol komunikasi: UDP SIYI
- Media streaming: RTSP dan HTTP media helper

## 3. Data Jaringan Default

- IP kamera default: 192.168.144.25
- IP modul AI default: 192.168.144.60
- Port UDP default: 37260
- RTSP utama: rtsp://192.168.144.25:8554/video1
- RTSP sub: rtsp://192.168.144.25:8554/video2
- Web media: http://192.168.144.25:82//cgi-bin/media.cgi

## 4. Konfigurasi Jaringan yang Disarankan

- Atur IP komputer/companion computer berada di subnet yang sama dengan ZT30
- Contoh konfigurasi:
  - IP address: 192.168.144.30
  - Netmask: 255.255.255.0
  - Gateway: kosong atau 192.168.144.1

### Pengujian koneksi

```bash
ping 192.168.144.25
ping 192.168.144.60
```

## 5. Persyaratan Sistem

- Python 3.9 atau lebih baru
- Paket UI PyQt5
- FFmpeg untuk dekode RTSP pada dashboard

### Instalasi dependensi UI

```bash
python3 -m pip install ".[ui]"
```

### Instalasi FFmpeg

```bash
sudo apt install ffmpeg
```

## 6. Langkah Instalasi dan Jalankan Aplikasi

### Setup lingkungan

```bash
./setup.sh
```

### Jalankan dashboard

```bash
./run_dashboard.sh
```

### Jalankan UI langsung

```bash
python3 ui/zt30_dashboard_qt.py
```

### Build AppImage

```bash
./install.sh
./ZT30Control.AppImage
```

## 7. Struktur UI Utama

### 7.1 Bagian Connection

Kontrol yang tersedia:
- Camera IP
- UDP Port
- Tombol Connect UDP
- Tombol Connect + Play

### 7.2 Bagian Quick Actions

- Photo: mengambil foto
- Record: menyala/mematikan record
- Auto Focus: menjalankan autofocus

### 7.3 Bagian Gimbal

- Slider Speed: mengatur kecepatan rotasi gimbal
- Tombol pan/tilt arah (Up, Down, Left, Right)
- Tombol Center: mengembalikan gimbal ke posisi tengah
- Mode kontrol:
  - Lock
  - Follow
  - FPV
- Set Angle: mengatur sudut yaw dan pitch tertentu

### 7.4 Bagian Camera

- Zoom +/-: mengontrol zoom manual
- Set Zoom: mengatur nilai zoom absolut
- Near Focus / Far Focus: kontrol fokus
- View: memilih mode tampilan kamera (contoh: Zoom + Thermal, Wide + Thermal, Split Zoom/Wide)
- Thermal: memilih palette termal
- Point / Full: memanggil data suhu titik atau full image

### 7.5 Bagian Laser Rangefinder

- On / Off: mengaktifkan atau mematikan laser
- Range: mengukur jarak
- GPS: mengirim target GPS

### 7.6 Bagian Media

- Photos: menampilkan foto
- Videos: menampilkan video
- Refresh: memperbarui daftar media
- Open / Download: membuka atau mengunduh media terpilih

### 7.7 Bagian AI Tracking

- AI IP
- Port
- Connect AI
- Rec On / Rec Off
- Status
- Track Center
- Cancel
- Overlay

### 7.8 Bagian Joystick

- Mendukung perangkat joystick di /dev/input/js0
- Axis 4 untuk pan
- Axis 5 untuk tilt
- Deadzone dapat diatur

### 7.9 Bagian Status

Menampilkan informasi real-time:
- Yaw
- Pitch
- Roll
- Zoom

## 8. Fitur Aplikasi yang Tersedia

### Kontrol Gimbal
- Rotasi manual
- Centering
- Pengaturan sudut absolut
- Mode lock/follow/fpv

### Kontrol Kamera
- Zoom manual dan absolut
- Fokus manual dan autofocus
- Pengambilan foto
- Toggle rekam

### Visualisasi dan Streaming
- Tampilan utama live view
- Optional Picture-in-Picture (Video 2)
- Dukungan RTSP playback melalui ffmpeg

### AI Tracking
- Mengaktifkan/mematikan pengenalan AI
- Melacak target berdasarkan titik tengah atau overlay klik
- Menampilkan status tracking
- Menggunakan koordinat 1280x720 untuk area klik live view

### Thermal dan Laser
- Pengambilan suhu titik dan full image
- Pemilihan palette termal
- Kontrol laser rangefinder

## 9. Contoh Penggunaan SDK

### Kontrol dasar

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

### AI Tracking

```python
from siyi_zt30 import SiyiAITrackingClient

ai = SiyiAITrackingClient("192.168.144.60")
print(ai.request_firmware_version())
ai.set_recognition_enabled(True)
ai.track_point(640, 360)
ai.set_coordinate_stream_enabled(True)
ai.start_coordinate_listener(lambda box: print(box))
ai.close()
```

## 10. Catatan Operasional Penting

- Jika panel kontrol tidak bisa terhubung, pastikan IP dan port benar.
- Jika joystick tidak berfungsi, cek hak akses ke /dev/input/js0.
- Jika dashboard melaporkan permission denied pada joystick, jalankan perintah berikut:

```bash
sudo usermod -aG input "$USER"
```

- Jika ffmpeg tidak ditemukan, instal terlebih dahulu sebelum menjalankan streaming.
- Untuk AI tracking, gunakan field AI IP jika alamat modul AI berbeda dari default 192.168.144.60.

## 11. Daftar File Utama Relevan

- README.md
- ui/zt30_dashboard_qt.py
- examples/basic_control.py
- examples/ai_tracking.py
- siyi_zt30/constants.py

## 12. Ringkasan Singkat untuk Manual Pengguna

ZT30Control adalah aplikasi kontrol dan monitoring untuk gimbal kamera SIYI ZT30. Aplikasi ini memungkinkan pengguna mengoperasikan gimbal, mengatur zoom/fokus, mengambil foto, merekam, memantau stream video, mengendalikan AI tracking, serta mengakses data thermal dan laser melalui antarmuka sederhana berbasis PyQt5.
