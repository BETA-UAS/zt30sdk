"""HTTP web server helper for files stored on UniPod MT11 TF card."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional
from urllib.parse import urlencode
from urllib.request import urlopen

from .constants import DEFAULT_WEB_BASE


class MT11WebClient:
    """Minimal client for MT11 media web server on port 82."""

    def __init__(self, base_url: str = DEFAULT_WEB_BASE, timeout: float = 3.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}{path}?{urlencode(params)}"
        with urlopen(url, timeout=self.timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw)

    def get_directories(self, media_type: int = 0) -> Dict[str, Any]:
        """media_type: 0 images, 1 videos."""
        return self._get("/api/v1/getdirectories", {"media_type": int(media_type)})

    def get_media_count(self, media_type: int = 0, path: str = "") -> Dict[str, Any]:
        return self._get("/api/v1/getmediacount", {"media_type": int(media_type), "path": path})

    def get_media_list(self, media_type: int = 0, path: str = "", start: int = 0, count: int = 20) -> Dict[str, Any]:
        return self._get("/api/v1/getmedialist", {"media_type": int(media_type), "path": path, "start": int(start), "count": int(count)})
