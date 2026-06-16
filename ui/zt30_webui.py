#!/usr/bin/env python3

from flask import Flask, Response, jsonify, request
import requests
from urllib.parse import urlparse

ZT30_HOST = "192.168.144.25"
ZT30_PORT = 82
ZT30_BASE = f"http://{ZT30_HOST}:{ZT30_PORT}/cgi-bin/media.cgi//api/v1"

app = Flask(__name__)


def zt30_get(endpoint, params=None):
    url = f"{ZT30_BASE}/{endpoint}"
    r = requests.get(url, params=params or {}, timeout=5)
    r.raise_for_status()
    return r.json()


def format_size(size_bytes):
    if size_bytes is None:
        return "Unknown"

    value = float(size_bytes)
    units = ["B", "KiB", "MiB", "GiB", "TiB"]

    for unit in units:
        if value < 1024:
            return f"{value:.2f} {unit}"
        value /= 1024

    return f"{value:.2f} PiB"


def get_remote_file_size(url):
    if not is_allowed_zt30_url(url):
        return None

    try:
        r = requests.head(url, timeout=5)
        r.close()

        content_length = r.headers.get("Content-Length")
        if content_length is not None:
            return int(content_length)
    except Exception:
        return None

    return None


def is_allowed_zt30_url(url):
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in ("http", "https"):
        return False

    if parsed.hostname != ZT30_HOST:
        return False

    return True


@app.route("/")
def index():
    return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>ZT30 Media Browser</title>
  <style>
    body {
      font-family: Arial, sans-serif;
      margin: 24px;
      background: #f5f5f5;
      color: #222;
    }

    h1 {
      margin-bottom: 8px;
    }

    button {
      padding: 8px 12px;
      margin-right: 8px;
      cursor: pointer;
      border: 1px solid #bbb;
      border-radius: 4px;
      background: #fff;
    }

    button:hover {
      background: #eee;
    }

    .card {
      background: white;
      padding: 16px;
      border-radius: 8px;
      margin-top: 16px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.12);
    }

    .topbar {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      margin-top: 12px;
    }

    .status {
      margin-top: 12px;
      color: #555;
      font-size: 14px;
    }

    .item {
      padding: 10px 8px;
      border-bottom: 1px solid #ddd;
    }

    .item:last-child {
      border-bottom: none;
    }

    .filename {
      font-weight: bold;
      margin-bottom: 4px;
    }

    .meta {
      color: #666;
      font-size: 13px;
      margin-bottom: 6px;
    }

    a {
      color: #0645ad;
      text-decoration: none;
    }

    a:hover {
      text-decoration: underline;
    }

    pre {
      background: #111;
      color: #eee;
      padding: 12px;
      overflow: auto;
      border-radius: 6px;
    }
  </style>
</head>
<body>
  <h1>ZT30 Media Browser</h1>
  <p>Camera: 192.168.144.25</p>

  <div class="topbar">
    <button onclick="loadMedia(0)">Load Photos</button>
    <button onclick="loadMedia(1)">Load Videos</button>
    <button onclick="refreshLast()">Refresh</button>
  </div>

  <div class="status" id="status">Ready.</div>

  <div class="card">
    <h2>Files</h2>
    <div id="files">Click Load Photos or Load Videos.</div>
  </div>

<script>
let lastMediaType = null;

function setStatus(text) {
  document.getElementById("status").innerText = text;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function refreshLast() {
  if (lastMediaType === null) {
    setStatus("No media type selected yet.");
    return;
  }

  await loadMedia(lastMediaType);
}

async function loadMedia(mediaType) {
  lastMediaType = mediaType;

  const filesDiv = document.getElementById("files");
  const label = mediaType === 0 ? "photos" : "videos";

  filesDiv.innerHTML = "Loading...";
  setStatus(`Loading ${label}...`);

  try {
    const dirResp = await fetch(`/api/directories?media_type=${mediaType}`);
    const dirJson = await dirResp.json();

    if (!dirJson.success || !dirJson.data || !dirJson.data.directories || !dirJson.data.directories.length) {
      filesDiv.innerHTML = "<pre>" + escapeHtml(JSON.stringify(dirJson, null, 2)) + "</pre>";
      setStatus(`No ${label} directory found.`);
      return;
    }

    const path = dirJson.data.directories[0].path;

    const countResp = await fetch(`/api/count?media_type=${mediaType}&path=${encodeURIComponent(path)}`);
    const countJson = await countResp.json();

    let totalCount = "Unknown";
    if (countJson.success && countJson.data && countJson.data.count !== undefined) {
      totalCount = countJson.data.count;
    }

    const listResp = await fetch(`/api/list?media_type=${mediaType}&path=${encodeURIComponent(path)}&start=0&count=100`);
    const listJson = await listResp.json();

    if (!listJson.success) {
      filesDiv.innerHTML = "<pre>" + escapeHtml(JSON.stringify(listJson, null, 2)) + "</pre>";
      setStatus(`Failed to load ${label}.`);
      return;
    }

    const list = listJson.data.list || [];
    if (!list.length) {
      filesDiv.innerHTML = "No files found.";
      setStatus(`Directory: ${path} | Total: ${totalCount}`);
      return;
    }

    filesDiv.innerHTML = list.map(file => {
      const proxyUrl = `/media?url=${encodeURIComponent(file.url)}`;
      const name = escapeHtml(file.name || "Unnamed");
      const sizeText = escapeHtml(file.size_text || "Unknown");
      const sizeBytes = file.size_bytes !== null && file.size_bytes !== undefined
        ? `${file.size_bytes} bytes`
        : "Unknown bytes";

      return `
        <div class="item">
          <div class="filename">${name}</div>
          <div class="meta">Size: ${sizeText} | ${escapeHtml(sizeBytes)}</div>
          <a href="${proxyUrl}" target="_blank">Open</a>
          |
          <a href="${proxyUrl}" download="${name}">Download</a>
        </div>
      `;
    }).join("");

    setStatus(`Directory: ${path} | Showing: ${list.length} | Total: ${totalCount}`);
  } catch (err) {
    filesDiv.innerHTML = "<pre>" + escapeHtml(String(err)) + "</pre>";
    setStatus("Error while loading media.");
  }
}
</script>
</body>
</html>
"""


@app.route("/api/directories")
def api_directories():
    media_type = request.args.get("media_type", "0")

    try:
        return jsonify(zt30_get("getdirectories", {"media_type": media_type}))
    except Exception as e:
        return jsonify({
            "success": False,
            "message": str(e),
        }), 500


@app.route("/api/count")
def api_count():
    media_type = request.args.get("media_type", "0")
    path = request.args.get("path", "")

    try:
        return jsonify(zt30_get("getmediacount", {
            "media_type": media_type,
            "path": path,
        }))
    except Exception as e:
        return jsonify({
            "success": False,
            "message": str(e),
        }), 500


@app.route("/api/list")
def api_list():
    media_type = request.args.get("media_type", "0")
    path = request.args.get("path", "")
    start = request.args.get("start", "0")
    count = request.args.get("count", "100")

    try:
        data = zt30_get("getmedialist", {
            "media_type": media_type,
            "path": path,
            "start": start,
            "count": count,
        })

        if data.get("success") and "data" in data:
            for item in data["data"].get("list", []):
                url = item.get("url")
                size = get_remote_file_size(url) if url else None
                item["size_bytes"] = size
                item["size_text"] = format_size(size)

        return jsonify(data)

    except Exception as e:
        return jsonify({
            "success": False,
            "message": str(e),
        }), 500


@app.route("/api/filesize")
def api_filesize():
    url = request.args.get("url")
    if not url:
        return jsonify({
            "success": False,
            "message": "Missing url",
        }), 400

    if not is_allowed_zt30_url(url):
        return jsonify({
            "success": False,
            "message": "URL is not allowed",
        }), 400

    size = get_remote_file_size(url)

    return jsonify({
        "success": True,
        "url": url,
        "size_bytes": size,
        "size_text": format_size(size),
    })


@app.route("/media")
def proxy_media():
    url = request.args.get("url")
    if not url:
        return "Missing url", 400

    if not is_allowed_zt30_url(url):
        return "URL is not allowed", 400

    try:
        r = requests.get(url, stream=True, timeout=10)

        headers = {}
        if "Content-Length" in r.headers:
            headers["Content-Length"] = r.headers["Content-Length"]

        if "Content-Disposition" in r.headers:
            headers["Content-Disposition"] = r.headers["Content-Disposition"]

        return Response(
            r.iter_content(chunk_size=1024 * 64),
            content_type=r.headers.get("Content-Type", "application/octet-stream"),
            headers=headers,
        )

    except Exception as e:
        return f"Failed to fetch media: {e}", 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)