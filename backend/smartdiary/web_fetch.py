"""Optional public article extraction. DNS is pinned to checked public addresses."""

import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup


def checked_addresses(host):
    addresses = list({item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError("只支持公网网页")
    return addresses


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, ip):
        super().__init__(host, timeout=10, context=ssl.create_default_context())
        self.ip = ip

    def connect(self):
        sock = socket.create_connection((self.ip, 443), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def extract(url):
    for _ in range(3):
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.port not in {None, 443}
        ):
            raise ValueError("仅支持不含登录信息的 HTTPS 网页")
        addresses = checked_addresses(parts.hostname)
        conn = PinnedHTTPS(parts.hostname, addresses[0])
        try:
            conn.request(
                "GET",
                (parts.path or "/") + ("?" + parts.query if parts.query else ""),
                headers={
                    "User-Agent": "SmartDiary/0.1 (user-requested article capture)",
                    "Accept": "text/html",
                },
            )
            response = conn.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                url = urljoin(url, response.getheader("Location", ""))
                continue
            if response.status != 200 or "text/html" not in response.getheader("Content-Type", ""):
                raise ValueError("网页不可读取")
            data = response.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise ValueError("网页过大")
            soup = BeautifulSoup(data, "html.parser")
            for node in soup(["script", "style", "nav", "footer", "header"]):
                node.decompose()
            root = soup.find("article") or soup.find("main") or soup.body or soup
            return root.get_text("\n", strip=True)[:20000]
        finally:
            conn.close()
    raise ValueError("网页重定向过多")
