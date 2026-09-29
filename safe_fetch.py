"""Bounded, DNS-pinned HTTP fetches for user-supplied public web URLs.

The connection is opened to a public address selected during validation while
the original hostname remains the HTTP Host header and TLS SNI/certificate
name.  That closes the DNS-rebinding gap present in the original manual-job
preview fetcher without process-global DNS monkeypatching.
"""
from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import urljoin, urlparse


HOSTNAME = re.compile(
    r'^(?=.{1,253}$)[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?'
    r'(?:\.[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?)+$', re.I)


class SafeFetchError(ValueError):
    """A safe, user-displayable reason a public page was not fetched."""


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    content_type: str
    text: str
    headers: dict
    pinned_ip: str


def valid_hostname(host):
    text = str(host or '').strip().rstrip('.')
    if not text:
        return False
    try:
        ipaddress.ip_address(text)
        return True
    except ValueError:
        return bool(HOSTNAME.match(text))


def _normal_ip(value):
    address = ipaddress.ip_address(str(value))
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def is_public_ip(value):
    try:
        address = _normal_ip(value)
    except ValueError:
        return False
    return not (address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast
                or address.is_unspecified)


def resolve_public_addresses(hostname, resolver=socket.getaddrinfo):
    """Resolve and validate every address for a hostname before selecting one."""
    host = str(hostname or '').strip().rstrip('.').lower()
    if (not host or host == 'localhost'
            or host.endswith(('.local', '.internal', '.lan', '.home'))
            or not valid_hostname(host)):
        raise SafeFetchError('That address is not a public web host.')
    try:
        answers = resolver(host, None, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as error:
        raise SafeFetchError('That address could not be resolved safely.') from error
    addresses = []
    for answer in answers:
        try:
            address = _normal_ip(answer[4][0])
        except (IndexError, ValueError):
            raise SafeFetchError('That address could not be resolved safely.')
        if not is_public_ip(address):
            raise SafeFetchError('That address points somewhere on the local network, so it was not read.')
        text = str(address)
        if text not in addresses:
            addresses.append(text)
    if not addresses:
        raise SafeFetchError('That address could not be resolved safely.')
    return addresses


def public_host(hostname, resolver=socket.getaddrinfo):
    try:
        resolve_public_addresses(hostname, resolver=resolver)
        return True
    except SafeFetchError:
        return False


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, port, pinned_ip, timeout):
        super().__init__(host, port=port, timeout=timeout)
        self.pinned_ip = pinned_ip

    def connect(self):
        self.sock = socket.create_connection((self.pinned_ip, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, port, pinned_ip, timeout):
        super().__init__(host, port=port, timeout=timeout,
                         context=ssl.create_default_context())
        self.pinned_ip = pinned_ip

    def connect(self):
        raw = socket.create_connection((self.pinned_ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def _connection_for(scheme, host, port, pinned_ip, timeout):
    if scheme == 'https':
        return _PinnedHTTPSConnection(host, port, pinned_ip, timeout)
    return _PinnedHTTPConnection(host, port, pinned_ip, timeout)


def _content_type(headers):
    return str(headers.get('content-type') or '').split(';', 1)[0].strip().lower()


def _encoding(headers):
    match = re.search(r'charset=([^;\s]+)', str(headers.get('content-type') or ''), re.I)
    return match.group(1).strip('"\'') if match else 'utf-8'


def fetch_text(url, *, method='GET', body=None, headers=None, timeout=10,
               max_redirects=3, max_bytes=2 * 1024 * 1024,
               allowed_schemes=('https',), allowed_ports=None,
               allowed_content=('text/html',), allowed_hosts=None,
               user_agent='JobSearch/1.0 (+https://fritsch-nas.myasustor.com/)',
               resolver=socket.getaddrinfo, connection_factory=_connection_for):
    """Fetch a bounded text response through a connection pinned to validated DNS.

    `allowed_hosts` is checked on every redirect and should contain the initial
    public hostname when traversing employer listing/detail pages.
    """
    current = str(url or '').strip()
    if not current:
        raise SafeFetchError('A web address is required.')
    allowed_schemes = {str(value).lower() for value in allowed_schemes}
    allowed_ports = set(allowed_ports or (80, 443))
    allowed_hosts = ({str(value).lower().rstrip('.') for value in allowed_hosts}
                     if allowed_hosts else None)
    request_headers = {
        'User-Agent': user_agent,
        'Accept': 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.5',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'identity',
    }
    request_headers.update(headers or {})
    request_body = body.encode('utf-8') if isinstance(body, str) else body

    for _ in range(max_redirects + 1):
        parsed = urlparse(current)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or '').lower().rstrip('.')
        try:
            port = parsed.port or (443 if scheme == 'https' else 80)
        except ValueError as error:
            raise SafeFetchError('That address has an invalid port.') from error
        if scheme not in allowed_schemes:
            raise SafeFetchError('That address uses an unsupported protocol.')
        if port not in allowed_ports:
            raise SafeFetchError('That address uses an unsupported port.')
        if allowed_hosts is not None and host not in allowed_hosts:
            raise SafeFetchError('That address left the approved public host.')
        pinned_ip = resolve_public_addresses(host, resolver=resolver)[0]
        target = (parsed.path or '/') + (('?' + parsed.query) if parsed.query else '')
        connection = None
        try:
            connection = connection_factory(scheme, host, port, pinned_ip, timeout)
            connection.request(method, target, body=request_body, headers=request_headers)
            response = connection.getresponse()
            # http.client preserves each server's original header-name casing (unlike
            # requests' CaseInsensitiveDict), so lookups below must go through a
            # lowercased dict rather than assuming e.g. "Content-Type"/"Location".
            response_headers = {key.lower(): value for key, value in response.getheaders()}
            if response.status in (301, 302, 303, 307, 308) and response_headers.get('location'):
                if method.upper() != 'GET':
                    raise SafeFetchError('That address redirected a non-read request.')
                current = urljoin(current, response_headers['location'])
                response.read()
                continue
            if response.status >= 400:
                raise SafeFetchError('The site returned HTTP {} for that address.'.format(response.status))
            content_type = _content_type(response_headers)
            if allowed_content and not any(content_type.startswith(value) for value in allowed_content):
                raise SafeFetchError('That address returned {}, which is not an allowed document type.'.format(content_type or 'an unknown type'))
            chunks, total = [], 0
            while total < max_bytes:
                chunk = response.read(min(8192, max_bytes - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            raw = b''.join(chunks)
            return FetchResult(current, response.status, content_type,
                               raw.decode(_encoding(response_headers), errors='replace'),
                               response_headers, pinned_ip)
        except SafeFetchError:
            raise
        except (OSError, http.client.HTTPException, ssl.SSLError) as error:
            raise SafeFetchError('Could not reach that address safely.') from error
        finally:
            if connection is not None:
                connection.close()
    raise SafeFetchError('That address redirected too many times.')
