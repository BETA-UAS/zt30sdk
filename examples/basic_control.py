#!/usr/bin/env python3

from mt11_sdk import MT11UDPClient


def main():
    cam = MT11UDPClient("192.168.144.25")

    try:
        print("Firmware:", cam.request_firmware_version())
        print("Hardware ID:", cam.request_hardware_id())
        print("Attitude:", cam.request_attitude())
        print("Laser status:", cam.request_laser_status())

        print("Center:", cam.center())
        print("Zoom:", cam.request_zoom())
    finally:
        cam.close()


if __name__ == "__main__":
    main()
