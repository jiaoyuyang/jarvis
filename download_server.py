#!/usr/bin/env python3
import json
import mimetypes
import os
import time
from pathlib import Path
from urllib.parse import quote

from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

from core.paths import DATA_DIR, OUTPUT_DIR as CONFIGURED_OUTPUT_DIR

TOKEN_FILE = DATA_DIR / "download_tokens.json"
OUTPUT_DIR = CONFIGURED_OUTPUT_DIR.resolve()


def load_tokens():
    if not TOKEN_FILE.exists():
        return {}
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def is_allowed(path):
    try:
        p = Path(path).resolve()
        return p.exists() and p.is_file() and str(p).startswith(str(OUTPUT_DIR) + "/")
    except Exception:
        return False


class DownloadHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def send_text(self, status, text):
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.path.startswith("/d/"):
            self.send_text(404, "Not found")
            return

        token = self.path.split("/d/", 1)[1].split("?", 1)[0].strip()
        if not token:
            self.send_text(400, "Missing token")
            return

        tokens = load_tokens()
        item = tokens.get(token)
        if not item:
            self.send_text(404, "Download link not found or expired")
            return

        if int(item.get("expires_at", 0)) < int(time.time()):
            self.send_text(410, "Download link expired")
            return

        file_path = item.get("path")
        if not is_allowed(file_path):
            self.send_text(403, "File not allowed")
            return

        p = Path(file_path).resolve()
        content_type = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        filename = p.name

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(p.stat().st_size))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}")
        self.end_headers()

        with p.open("rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)


def main():
    host = os.getenv("DOWNLOAD_BIND_HOST", "127.0.0.1")
    port = int(os.getenv("DOWNLOAD_BIND_PORT", "18080"))
    server = ThreadingHTTPServer((host, port), DownloadHandler)
    print(f"download server listening on {host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
