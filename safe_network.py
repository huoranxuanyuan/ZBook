# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded public web requests, using the user's standard proxy configuration.

Direct sockets connect only to the public IP addresses validated for that
connection. Explicit proxies own DNS/routing; private proxy endpoints are valid.
No proxy credentials, addresses or application-specific exceptions are stored.
"""

import ipaddress
import socket
import time
import threading
from urllib.parse import urlparse, urljoin
import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import NewConnectionError
from urllib3.util.connection import create_connection


def public_url(url):
    p = urlparse(url)
    host = (p.hostname or "").rstrip(".").lower()
    if (
        p.scheme not in ("http", "https")
        or not host
        or p.username is not None
        or p.password is not None
        or p.port not in (None, 80, 443)
    ):
        raise ValueError("仅支持公开的 HTTP/HTTPS 地址")
    if (
        len(host) > 253
        or "%" in host
        or host == "localhost"
        or host.endswith((".localhost", ".local", ".internal"))
        or ("." not in host and ":" not in host)
    ):
        raise ValueError("拒绝本地或无效的网络地址")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("拒绝非公开网络地址")
    return p


def public_addresses(host, port):
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("拒绝解析到非公开地址的主机；代理请使用系统或环境变量配置")
    return list(dict.fromkeys(item[4][0] for item in addresses))


def _shutdown(sock):
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


class _HeaderDeadline:
    def getresponse(self):
        # A peer sending one header byte at a time must not reset the timeout forever.
        delay = min(12, self.timeout) if isinstance(self.timeout, (int, float)) else 12
        timer = threading.Timer(max(0.001, delay), _shutdown, args=(self.sock,))
        timer.daemon = True
        timer.start()
        try:
            return super().getresponse()
        finally:
            timer.cancel()


class BoundedHTTPConnection(_HeaderDeadline, HTTPConnection):
    pass


class BoundedHTTPSConnection(_HeaderDeadline, HTTPSConnection):
    pass


class BoundedHTTPPool(HTTPConnectionPool):
    ConnectionCls = BoundedHTTPConnection


class BoundedHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = BoundedHTTPSConnection


class _PublicConnection:
    def _new_conn(self):
        # Resolve exactly once, then pass numeric IPs to connect. TLS still uses
        # self.host for SNI and certificate verification, with no verification bypass.
        addresses = public_addresses(self._dns_host, self.port)
        error = None
        for address in addresses:
            try:
                return create_connection(
                    (address, self.port),
                    self.timeout,
                    source_address=self.source_address,
                    socket_options=self.socket_options,
                )
            except OSError as exc:
                error = exc
        raise NewConnectionError(self, "连接公开主机失败") from error


class PublicHTTPConnection(_PublicConnection, BoundedHTTPConnection):
    pass


class PublicHTTPSConnection(_PublicConnection, BoundedHTTPSConnection):
    pass


class PublicHTTPPool(HTTPConnectionPool):
    ConnectionCls = PublicHTTPConnection


class PublicHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = PublicHTTPSConnection


class PublicAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        # Per-manager copy; never modify urllib3 globals or the proxy manager.
        self.poolmanager.pool_classes_by_scheme = {"http": PublicHTTPPool, "https": PublicHTTPSPool}

    def proxy_manager_for(self, proxy, **kwargs):
        manager = super().proxy_manager_for(proxy, **kwargs)
        if proxy.lower().startswith("socks"):
            from urllib3.contrib.socks import (
                SOCKSHTTPConnection,
                SOCKSHTTPSConnection,
                SOCKSHTTPConnectionPool,
                SOCKSHTTPSConnectionPool,
            )

            class HTTP(_HeaderDeadline, SOCKSHTTPConnection):
                pass

            class HTTPS(_HeaderDeadline, SOCKSHTTPSConnection):
                pass

            class HTTPPool(SOCKSHTTPConnectionPool):
                ConnectionCls = HTTP

            class HTTPSPool(SOCKSHTTPSConnectionPool):
                ConnectionCls = HTTPS

            manager.pool_classes_by_scheme = {"http": HTTPPool, "https": HTTPSPool}
        else:
            manager.pool_classes_by_scheme = {"http": BoundedHTTPPool, "https": BoundedHTTPSPool}
        return manager


class PublicSession(requests.Session):
    def __init__(self):
        super().__init__()
        for scheme in ("http://", "https://"):
            self.mount(scheme, PublicAdapter())


def get(url, params=None, headers=None, session=None, max_bytes=8_000_000, budget=30):
    owned = session is None
    client = session or PublicSession()
    deadline = time.monotonic() + budget
    try:
        for hop in range(5):
            public_url(url)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise requests.Timeout("网络请求超过总时限")
            response = client.get(
                url,
                params=params,
                headers=headers,
                stream=True,
                allow_redirects=False,
                timeout=(min(5, remaining), min(9, remaining)),
            )
            connection = getattr(response.raw, "_connection", None)
            transport = getattr(connection, "sock", None)
            if transport is None:
                # HTTP/1.0 / Connection: close detach the pool connection while
                # the response file still owns the readable socket.
                reader = getattr(getattr(response.raw, "_fp", None), "fp", None)
                transport = getattr(getattr(reader, "raw", None), "_sock", None)
            # Closing the socket interrupts iter_content even before its first
            # 64 KiB chunk. A check only between chunks would allow slow-drip DoS.
            timer = threading.Timer(
                max(0.001, deadline - time.monotonic()), _shutdown, args=(transport,)
            )
            timer.daemon = True
            timer.start()
            try:
                if response.is_redirect:
                    url = urljoin(response.url, response.headers["Location"])
                    params = None
                    continue
                response.raise_for_status()
                length = response.headers.get("Content-Length", "")
                if length.isdigit() and int(length) > max_bytes:
                    raise ValueError("网络响应过大")
                data = bytearray()
                for chunk in response.iter_content(65536):
                    if time.monotonic() > deadline:
                        raise requests.Timeout("网络请求超过总时限")
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        raise ValueError("网络响应解压后超过大小限制")
                if time.monotonic() > deadline:
                    raise requests.Timeout("网络请求超过总时限")
                response._content = bytes(data)
                response._content_consumed = True
                return response
            except (OSError, requests.RequestException) as exc:
                if time.monotonic() > deadline:
                    raise requests.Timeout("网络请求超过总时限") from exc
                raise
            finally:
                timer.cancel()
                response.close()
        raise ValueError("网络重定向次数过多")
    finally:
        if owned:
            client.close()
