#!/usr/bin/env python3

from siyi_zt30 import ZT30UDPClient


def main():
    cam = ZT30UDPClient("192.168.144.25")
    print("Firmware:", cam.request_firmware_version())
    print("Hardware ID:", cam.request_hardware_id())
    print("Center:", cam.center())
    print("Attitude:", cam.request_attitude())
    print("Zoom:", cam.request_zoom())
    print("Laser status:", cam.request_laser_status())
    print("Laser range:", cam.request_laser_range())
    cam.close()


if __name__ == "__main__":
    main()
