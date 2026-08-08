"""Outbound HTTP helper for operator-supplied URLs.

The client intentionally follows redirects itself so every hop is validated
before another request is issued.  It never inherits proxy/cookie state from
ACP's process environment.
"""
from dataclasses import dataclass
import ipaddress
import socket
from typing import Iterable, Optional, Set
from urllib.parse import urljoin, urlsplit

import requests


class SafeHttpError(Exception):
    """A user-supplied URL could not be fetched safely."""


@dataclass(frozen=True)
class SafeHttpResponse:
    final_url: str
    content: bytes
    content_type: str


class SafeHttpClient:
    def __init__(self, session=None, dns_resolver=socket.getaddrinfo,
                 max_redirects: int = 5, max_bytes: int = 1_500_000,
                 timeout=(3, 8), user_agent: str = "ACP/2.0 product-metadata"):
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": user_agent})
        self.dns_resolver = dns_resolver
        self.max_redirects = max_redirects
        self.max_bytes = max_bytes
        self.timeout = timeout

    @staticmethod
    def _normalise_allowed_hosts(allowed_hosts: Optional[Iterable[str]]) -> Optional[Set[str]]:
        if allowed_hosts is None:
            return None
        return {h.lower().rstrip(".") for h in allowed_hosts}

    def validate_url(self, url: str, allowed_hosts=None) -> str:
        try:
            parsed = urlsplit(url)
        except ValueError as exc:
            raise SafeHttpError("URL không hợp lệ") from exc
        if parsed.scheme.lower() not in ("http", "https"):
            raise SafeHttpError("Chỉ hỗ trợ URL http/https")
        if parsed.username or parsed.password:
            raise SafeHttpError("URL không được chứa user/password")
        if not parsed.hostname:
            raise SafeHttpError("URL thiếu hostname")

        host = parsed.hostname.lower().rstrip(".")
        allowed = self._normalise_allowed_hosts(allowed_hosts)
        if allowed is not None and host not in allowed:
            raise SafeHttpError("Hostname chưa được hỗ trợ")

        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise SafeHttpError("Không chấp nhận IP literal")

        try:
            port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        except ValueError as exc:
            raise SafeHttpError("Port không hợp lệ") from exc
        try:
            infos = self.dns_resolver(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise SafeHttpError("Không resolve được hostname") from exc
        if not infos:
            raise SafeHttpError("Không resolve được hostname")

        for info in infos:
            raw_addr = info[4][0]
            # IPv6 sockaddr may include a scope suffix, which ipaddress does not accept.
            raw_addr = raw_addr.split("%", 1)[0]
            try:
                addr = ipaddress.ip_address(raw_addr)
            except ValueError as exc:
                raise SafeHttpError("DNS trả địa chỉ IP không hợp lệ") from exc
            if (addr.is_private or addr.is_loopback or addr.is_link_local or
                    addr.is_reserved or addr.is_multicast or addr.is_unspecified):
                raise SafeHttpError("URL trỏ vào địa chỉ mạng không được phép")
        return url

    def get(self, url: str, allowed_hosts=None, expected_content_prefix: str = None) -> SafeHttpResponse:
        current = url
        for hop in range(self.max_redirects + 1):
            self.validate_url(current, allowed_hosts)
            try:
                self.session.cookies.clear()
            except AttributeError:
                pass
            try:
                response = self.session.get(
                    current,
                    allow_redirects=False,
                    stream=True,
                    timeout=self.timeout,
                    headers={"Accept": "*/*"},
                )
            except requests.RequestException as exc:
                raise SafeHttpError("Không thể kết nối tới URL") from exc

            try:
                status = int(response.status_code)
                if 300 <= status < 400:
                    location = response.headers.get("Location")
                    if not location:
                        raise SafeHttpError("Redirect thiếu Location")
                    if hop >= self.max_redirects:
                        raise SafeHttpError("Quá nhiều redirect")
                    current = urljoin(current, location)
                    # Validation happens at the start of the next loop before I/O.
                    continue

                if not (200 <= status < 300):
                    raise SafeHttpError(f"Upstream HTTP {status}")

                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if expected_content_prefix and not content_type.startswith(expected_content_prefix.lower()):
                    raise SafeHttpError("Content-Type không phù hợp")

                buf = bytearray()
                for chunk in response.iter_content(65536):
                    if not chunk:
                        continue
                    buf.extend(chunk)
                    if len(buf) > self.max_bytes:
                        raise SafeHttpError("Response vượt giới hạn kích thước")
                return SafeHttpResponse(current, bytes(buf), content_type)
            finally:
                response.close()
        raise SafeHttpError("Quá nhiều redirect")
