"""HTTP web server helper for files stored on UniPod MT11 TF card."""

from __future__ import annotations

from html.parser import HTMLParser
import json
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError
from urllib.parse import unquote, urlencode, urljoin, urlparse
from urllib.request import urlopen

from .constants import DEFAULT_WEB_BASE


class _BoaIndexParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: List[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() != "a":
            return
        values = dict(attrs)
        href = values.get("href")
        if href and href != "../":
            self.links.append(href)


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

    def _get_api_or_fallback(self, path: str, params: Dict[str, Any], fallback):
        try:
            return self._get(path, params)
        except (HTTPError, json.JSONDecodeError):
            return fallback()

    def get_directories(self, media_type: int = 0) -> Dict[str, Any]:
        """media_type: 0 images, 1 videos."""
        return self._get_api_or_fallback(
            "/api/v1/getdirectories",
            {"media_type": int(media_type)},
            lambda: self._boa_directories(int(media_type)),
        )

    def get_media_count(self, media_type: int = 0, path: str = "") -> Dict[str, Any]:
        return self._get_api_or_fallback(
            "/api/v1/getmediacount",
            {"media_type": int(media_type), "path": path},
            lambda: {"success": True, "data": {"count": len(self._boa_media_items(path, int(media_type)))}},
        )

    def get_media_list(self, media_type: int = 0, path: str = "", start: int = 0, count: int = 20) -> Dict[str, Any]:
        return self._get_api_or_fallback(
            "/api/v1/getmedialist",
            {"media_type": int(media_type), "path": path, "start": int(start), "count": int(count)},
            lambda: self._boa_media_list(int(media_type), path, int(start), int(count)),
        )

    def _boa_roots(self, media_type: int) -> List[str]:
        if int(media_type) == 0:
            return ["/photo/", "/sd/DCIM/capture/"]
        return ["/sd/DCIM/record/"]

    def _boa_directories(self, media_type: int) -> Dict[str, Any]:
        directories = []
        for root in self._boa_roots(media_type):
            if self._boa_fetch_links(root) is not None:
                directories.append({"path": root})
        return {"success": True, "data": {"directories": directories}}

    def _boa_media_list(self, media_type: int, path: str = "", start: int = 0, count: int = 20) -> Dict[str, Any]:
        search_path = path or (self._boa_roots(media_type)[0])
        items = self._boa_media_items(search_path, media_type)
        return {"success": True, "data": {"list": items[start:start + count], "count": len(items)}}

    def _boa_media_items(self, path: str, media_type: int, max_depth: int = 3) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        visited = set()
        exts = {".jpg", ".jpeg", ".png", ".bmp"} if int(media_type) == 0 else {".mp4", ".mov", ".avi", ".ts", ".mkv"}

        def walk(folder: str, depth: int):
            folder = self._normalize_boa_path(folder)
            if folder in visited or depth < 0:
                return
            visited.add(folder)
            links = self._boa_fetch_links(folder)
            if links is None:
                return
            for href in links:
                if href.endswith("/"):
                    walk(urljoin(folder, href), depth - 1)
                    continue
                url_path = urljoin(folder, href)
                suffix = "." + unquote(urlparse(url_path).path).rsplit(".", 1)[-1].lower() if "." in url_path else ""
                if suffix not in exts:
                    continue
                name = unquote(url_path.rstrip("/").split("/")[-1])
                items.append({
                    "name": name,
                    "url": urljoin(self.base_url + "/", url_path.lstrip("/")),
                    "path": url_path,
                })

        walk(path or self._boa_roots(media_type)[0], max_depth)
        return items

    def _boa_fetch_links(self, path: str) -> Optional[List[str]]:
        url = urljoin(self.base_url + "/", self._normalize_boa_path(path).lstrip("/"))
        try:
            with urlopen(url, timeout=self.timeout) as response:
                content_type = response.headers.get("content-type", "")
                raw = response.read().decode("utf-8", errors="replace")
        except HTTPError:
            return None
        if "text/html" not in content_type.lower():
            return []
        parser = _BoaIndexParser()
        parser.feed(raw)
        return parser.links

    @staticmethod
    def _normalize_boa_path(path: str) -> str:
        path = path or "/"
        parsed = urlparse(path)
        if parsed.scheme and parsed.netloc:
            path = parsed.path
        if not path.startswith("/"):
            path = "/" + path
        if not path.endswith("/"):
            path += "/"
        return path
