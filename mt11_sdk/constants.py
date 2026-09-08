"""Constants for UniPod MT11 SDK."""

DEFAULT_IP = "192.168.144.25"
DEFAULT_AI_IP = DEFAULT_IP
DEFAULT_PORT = 37260
DEFAULT_RTSP_MAIN = "rtsp://192.168.144.25:8554/video1"
DEFAULT_RTSP_SUB = "rtsp://192.168.144.25:8554/video2"
DEFAULT_WEB_BASE = "http://192.168.144.25:82"

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
    "downward_view": 9,
    "zoom_linkage_toggle": 10,
}

IMAGE_MODES = {
    (0, 2): "zoom_sub_thermal",
    (2, 0): "thermal_sub_zoom",
    (3, 2): "zoom_thermal_sub_thermal",
}

IMAGE_MODE_BY_NAME = {v: k for k, v in IMAGE_MODES.items()}
MT11_STREAM_NAMES = {
    0: "zoom",
    1: "wide",
    2: "thermal",
    3: "zoom_thermal",
    4: "wide_thermal",
    5: "zoom_wide",
    6: "none",
}

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
