"""Bounded HTTPS downloads with host and network-range validation."""
from __future__ import annotations

import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests


DEFAULT_HOST_SUFFIXES = ("dingtalk.com", "aliyuncs.com")
MAX_BYTES = max(1024, min(int(os.getenv("JARVIS_DOWNLOAD_MAX_BYTES", str(25 * 1024 * 1024))), 100 * 1024 * 1024))
MAX_REDIRECTS = 3


def _allowed_suffixes() -> tuple[str, ...]:
    configured = os.getenv("JARVIS_DOWNLOAD_HOSTS", "")
    values = tuple(item.strip().lower().lstrip(".") for item in configured.split(",") if item.strip())
    return values or DEFAULT_HOST_SUFFIXES


def validate_download_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("download URL must be credential-free HTTPS")
    hostname = parsed.hostname.lower().rstrip(".")
    if not any(hostname == suffix or hostname.endswith("." + suffix) for suffix in _allowed_suffixes()):
        raise ValueError("download host is not allowlisted")

    addresses = {item[4][0] for item in socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)}
    if not addresses:
        raise ValueError("download host did not resolve")
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            raise ValueError("download host resolved to a non-public address")
    return parsed.geturl()


def download_to_path(url: str, destination: str | Path, *, max_bytes: int = MAX_BYTES) -> dict[str, object]:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    current_url = validate_download_url(url)

    try:
        for _redirect in range(MAX_REDIRECTS + 1):
            response = requests.get(current_url, timeout=(10, 60), stream=True, allow_redirects=False)
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("location")
                response.close()
                if not location:
                    raise ValueError("download redirect is missing a location")
                current_url = validate_download_url(urljoin(current_url, location))
                continue

            response.raise_for_status()
            declared = int(response.headers.get("content-length") or 0)
            if declared and declared > max_bytes:
                response.close()
                raise ValueError("download exceeds configured size limit")

            size = 0
            with partial.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("download exceeds configured size limit")
                    handle.write(chunk)
            content_type = response.headers.get("content-type", "")
            response.close()
            partial.replace(destination)
            return {"path": str(destination), "size": size, "content_type": content_type, "url": current_url}
        raise ValueError("download exceeded redirect limit")
    finally:
        if partial.exists():
            partial.unlink(missing_ok=True)
