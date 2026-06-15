"""Constants for SIYI ZT30 SDK."""

DEFAULT_IP = "192.168.144.25"
DEFAULT_PORT = 37260
DEFAULT_RTSP_MAIN = "rtsp://192.168.144.25:8554/video1"
DEFAULT_RTSP_SUB = "rtsp://192.168.144.25:8554/video2"
DEFAULT_WEB_BASE = "http://192.168.144.25:82//cgi-bin/media.cgi"

WORKING_MODES = {
    0: "lock",
    1: "follow",
    2: "fpv",
}

PHOTO_RECORD_FUNC = {
    "photo": 0,
    "hdr_toggle": 1,
    "record_toggle": 2,
    "lock_mode": 3,
    "follow_mode": 4,
    "fpv_mode": 5,
    "enable_hdmi": 6,
    "enable_cvbs": 7,
    "disable_hdmi_cvbs": 8,
}

IMAGE_MODES = {
    0: "split_zoom_thermal_sub_wide",
    1: "split_wide_thermal_sub_zoom",
    2: "split_zoom_wide_sub_thermal",
    3: "single_zoom_sub_thermal",
    4: "single_zoom_sub_wide",
    5: "single_wide_sub_thermal",
    6: "single_wide_sub_zoom",
    7: "single_thermal_sub_zoom",
    8: "single_thermal_sub_wide",
}

IMAGE_MODE_BY_NAME = {v: k for k, v in IMAGE_MODES.items()}

THERMAL_PALETTES = {
    0: "white_hot",
    1: "reserved",
    2: "sepia",
    3: "ironbow",
    4: "rainbow",
    5: "night",
    6: "aurora",
    7: "red_hot",
    8: "jungle",
    9: "medical",
    10: "black_hot",
    11: "glory_hot",
}

THERMAL_PALETTE_BY_NAME = {v: k for k, v in THERMAL_PALETTES.items()}

STREAM_TYPES = {
    0: "recording",
    1: "main",
    2: "sub",
}
STREAM_TYPE_BY_NAME = {v: k for k, v in STREAM_TYPES.items()}

VIDEO_ENCODERS = {
    1: "h264",
    2: "h265",
}
VIDEO_ENCODER_BY_NAME = {v: k for k, v in VIDEO_ENCODERS.items()}

DATA_FREQ = {
    0: "off",
    1: "2hz",
    2: "4hz",
    3: "5hz",
    4: "10hz",
    5: "20hz",
    6: "50hz",
    7: "100hz",
}
