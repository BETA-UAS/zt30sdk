#!/usr/bin/env python3
"""Basic UniPod MT11 AI tracking control example."""

from __future__ import annotations

import time

from mt11_sdk import DEFAULT_AI_IP, MT11AITrackingClient


def main():
    ai = MT11AITrackingClient(DEFAULT_AI_IP)

    try:
        print("firmware:", ai.request_firmware_version())
        print("recognition:", ai.set_recognition_enabled(True))

        print("track center:", ai.track_point(640, 360))
        print("coordinate stream:", ai.set_coordinate_stream_enabled(True))

        def on_box(box):
            print(
                f"target={box.target_type} state={box.state} "
                f"center=({box.x},{box.y}) size={box.width}x{box.height}"
            )

        ai.start_coordinate_listener(on_box)
        time.sleep(10)

        print("cancel:", ai.cancel_tracking())
        print("coordinate stream:", ai.set_coordinate_stream_enabled(False))

    finally:
        ai.close()


if __name__ == "__main__":
    main()
