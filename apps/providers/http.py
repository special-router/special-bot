from __future__ import annotations

import ipaddress
import re
import socket
import ssl
import subprocess
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from apps.providers.adapters.base import ProviderConfigurationError, ProviderTemporaryError


_HOST_RE = re.compile(
    r'^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+'
    r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$',
)
_DEFAULT_USER_AGENT = 'SPECIAL-provider-ingest/1'
_MAX_HEADER_BYTES = 64 * 1024
_MAX_CHUNK_OVERHEAD_BYTES = 256 * 1024
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_CONTENT_LENGTH_RE = re.compile(r'^[0-9]+$')
_CHUNK_SIZE_RE = re.compile(rb'^[0-9A-Fa-f]+$')
_UNIQUE_FRAMING_HEADERS = frozenset({'content-encoding', 'content-length', 'transfer-encoding'})


@dataclass(frozen=True)
class ProviderHTTPResponse:
    headers: dict[str, str]
    body: bytes


def valid_public_host(value: str) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(character in value for character in '\r\n\t /?#@')
    ):
        return False
    try:
        return ipaddress.ip_address(value).is_global
    except ValueError:
        return bool(_HOST_RE.fullmatch(value.casefold()))


def _is_public_unicast(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not any((
        address.is_multicast,
        address.is_unspecified,
        address.is_loopback,
        address.is_link_local,
        address.is_private,
        address.is_reserved,
        not address.is_global,
    ))


def _validated_user_agent(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or not all(' ' <= character <= '~' for character in value)
    ):
        raise ProviderConfigurationError('provider_user_agent_invalid')
    return value


def _validated_authorization(value: str) -> str:
    if not value:
        return ''
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or not value.startswith('Bearer ')
        or len(value) == len('Bearer ')
        or not all(' ' <= character <= '~' for character in value)
    ):
        raise ProviderConfigurationError('provider_authorization_invalid')
    return value


def _validated_url(url: str, allowed_hosts: frozenset[str]):
    if (
        not isinstance(url, str)
        or not 1 <= len(url) <= 4096
        or not url.isascii()
        or not all('!' <= character <= '~' for character in url)
        or '#' in url
    ):
        raise ProviderConfigurationError('provider_source_url_invalid')
    try:
        parsed = urlsplit(url)
        port = parsed.port or 443
    except (TypeError, ValueError):
        raise ProviderConfigurationError('provider_source_url_invalid') from None
    host = parsed.hostname.casefold() if parsed.hostname else ''
    if (
        parsed.scheme != 'https'
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or host not in allowed_hosts
        or not 1 <= port <= 65535
    ):
        raise ProviderConfigurationError('provider_source_url_invalid')
    return parsed, host, port


def _remaining(deadline: float, maximum: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ProviderTemporaryError('provider_fetch_deadline')
    return min(remaining, maximum)


def _resolve_public(host: str, deadline: float) -> set[str]:
    addresses = set()
    try:
        for database, family in (('ahostsv4', 4), ('ahostsv6', 6)):
            result = subprocess.run(
                ('getent', database, host),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=_remaining(deadline, 60),
                check=False,
            )
            for line in result.stdout.splitlines():
                candidate = line.split(maxsplit=1)[0] if line else ''
                try:
                    address = ipaddress.ip_address(candidate)
                except ValueError:
                    continue
                if address.version == family:
                    addresses.add(str(address))
    except (OSError, subprocess.TimeoutExpired):
        raise ProviderTemporaryError('provider_dns_failed') from None
    if not addresses:
        raise ProviderTemporaryError('provider_dns_failed')
    if any(not _is_public_unicast(address) for address in addresses):
        raise ProviderConfigurationError('provider_source_destination_invalid')
    return addresses


def _ordered_addresses(addresses: set[str]) -> tuple[str, ...]:
    return tuple(sorted(
        addresses,
        key=lambda value: (ipaddress.ip_address(value).version, int(ipaddress.ip_address(value))),
    ))


def _read_response(
    connection,
    *,
    deadline: float,
    read_timeout: float,
    max_bytes: int,
) -> tuple[int, dict[str, str], bytes]:
    raw = bytearray()
    while b'\r\n\r\n' not in raw:
        if len(raw) > _MAX_HEADER_BYTES:
            raise ProviderConfigurationError('provider_response_headers_too_large')
        connection.settimeout(_remaining(deadline, read_timeout))
        chunk = connection.recv(8192)
        if not chunk:
            raise ProviderTemporaryError('provider_response_incomplete')
        raw.extend(chunk)
    raw_headers, body = raw.split(b'\r\n\r\n', 1)
    if len(raw_headers) > _MAX_HEADER_BYTES:
        raise ProviderConfigurationError('provider_response_headers_too_large')
    try:
        lines = raw_headers.decode('iso-8859-1').split('\r\n')
        protocol, status, _reason = lines[0].split(' ', 2)
        if protocol not in {'HTTP/1.0', 'HTTP/1.1'}:
            raise ValueError
        headers = {}
        for line in lines[1:]:
            name, value = line.split(':', 1)
            normalized = name.strip().casefold()
            if not _HEADER_NAME_RE.fullmatch(name) or (
                normalized in headers and normalized in _UNIQUE_FRAMING_HEADERS
            ):
                raise ValueError
            headers.setdefault(normalized, value.strip())
        status_code = int(status)
        if not 100 <= status_code <= 599:
            raise ValueError
    except (UnicodeDecodeError, ValueError):
        raise ProviderConfigurationError('provider_response_invalid') from None

    if headers.get('content-encoding', '').casefold() not in {'', 'identity'}:
        raise ProviderConfigurationError('provider_response_encoding_unsupported')
    transfer_encoding = headers.get('transfer-encoding', '').casefold()
    declared = headers.get('content-length')
    if transfer_encoding and declared is not None:
        raise ProviderConfigurationError('provider_response_framing_invalid')
    if transfer_encoding not in {'', 'identity', 'chunked'}:
        raise ProviderConfigurationError('provider_response_framing_unsupported')
    if transfer_encoding == 'chunked':
        return status_code, headers, _read_chunked_body(
            connection,
            initial=body,
            deadline=deadline,
            read_timeout=read_timeout,
            max_bytes=max_bytes,
        )
    if declared is not None and not _CONTENT_LENGTH_RE.fullmatch(declared):
        raise ProviderConfigurationError('provider_response_size_invalid')
    expected_size = int(declared) if declared is not None else None
    if expected_size is not None and not 0 <= expected_size <= max_bytes:
        raise ProviderConfigurationError('provider_response_too_large')
    if len(body) > max_bytes:
        raise ProviderConfigurationError('provider_response_too_large')
    while expected_size is None or len(body) < expected_size:
        connection.settimeout(_remaining(deadline, read_timeout))
        chunk = connection.recv(min(8192, max_bytes - len(body) + 1))
        if not chunk:
            break
        body.extend(chunk)
        if len(body) > max_bytes:
            raise ProviderConfigurationError('provider_response_too_large')
    if expected_size is not None and len(body) != expected_size:
        raise ProviderTemporaryError('provider_response_incomplete')
    return status_code, headers, bytes(body)


def _read_chunked_body(
    connection,
    *,
    initial: bytearray,
    deadline: float,
    read_timeout: float,
    max_bytes: int,
) -> bytes:
    encoded = bytearray(initial)
    decoded = bytearray()
    cursor = 0

    def receive_until(predicate, error: str) -> None:
        while not predicate():
            if len(encoded) > max_bytes + _MAX_CHUNK_OVERHEAD_BYTES:
                raise ProviderConfigurationError('provider_response_too_large')
            connection.settimeout(_remaining(deadline, read_timeout))
            chunk = connection.recv(8192)
            if not chunk:
                raise ProviderTemporaryError(error)
            encoded.extend(chunk)

    while True:
        if len(encoded) > max_bytes + _MAX_CHUNK_OVERHEAD_BYTES:
            raise ProviderConfigurationError('provider_response_too_large')
        receive_until(lambda: encoded.find(b'\r\n', cursor) >= 0, 'provider_response_incomplete')
        line_end = encoded.find(b'\r\n', cursor)
        if line_end - cursor > 128:
            raise ProviderConfigurationError('provider_response_framing_invalid')
        size_token = bytes(encoded[cursor:line_end]).partition(b';')[0].strip()
        if not _CHUNK_SIZE_RE.fullmatch(size_token):
            raise ProviderConfigurationError('provider_response_framing_invalid')
        chunk_size = int(size_token, 16)
        if chunk_size > max_bytes - len(decoded):
            raise ProviderConfigurationError('provider_response_too_large')
        cursor = line_end + 2
        if chunk_size == 0:
            trailer_start = cursor
            while True:
                receive_until(lambda: encoded.find(b'\r\n', cursor) >= 0, 'provider_response_incomplete')
                trailer_end = encoded.find(b'\r\n', cursor)
                if trailer_end == cursor:
                    return bytes(decoded)
                if trailer_end - trailer_start > _MAX_HEADER_BYTES:
                    raise ProviderConfigurationError('provider_response_headers_too_large')
                trailer = bytes(encoded[cursor:trailer_end])
                if b':' not in trailer:
                    raise ProviderConfigurationError('provider_response_framing_invalid')
                cursor = trailer_end + 2
        required = cursor + chunk_size + 2
        receive_until(lambda: len(encoded) >= required, 'provider_response_incomplete')
        if encoded[cursor + chunk_size:required] != b'\r\n':
            raise ProviderConfigurationError('provider_response_framing_invalid')
        decoded.extend(encoded[cursor:cursor + chunk_size])
        cursor = required


def _fetch_from_address(
    *,
    destination: str,
    host: str,
    port: int,
    target: str,
    user_agent: str,
    authorization: str,
    deadline: float,
    connect_timeout: float,
    read_timeout: float,
    max_bytes: int,
) -> ProviderHTTPResponse:
    raw_socket = tls_socket = None
    try:
        raw_socket = socket.create_connection(
            (destination, port),
            timeout=_remaining(deadline, connect_timeout),
        )
        raw_socket.settimeout(_remaining(deadline, connect_timeout))
        tls_socket = ssl.create_default_context().wrap_socket(raw_socket, server_hostname=host)
        tls_socket.settimeout(_remaining(deadline, read_timeout))
        peer_ip = tls_socket.getpeername()[0]
        if ipaddress.ip_address(peer_ip) != ipaddress.ip_address(destination) or not _is_public_unicast(peer_ip):
            raise ProviderConfigurationError('provider_source_peer_invalid')
        try:
            host_is_ipv6 = ipaddress.ip_address(host).version == 6
        except ValueError:
            host_is_ipv6 = False
        authority = f'[{host}]' if host_is_ipv6 else host
        host_header = authority if port == 443 else f'{authority}:{port}'
        auth_header = f'Authorization: {authorization}\r\n' if authorization else ''
        request = (
            f'GET {target} HTTP/1.1\r\n'
            f'Host: {host_header}\r\n'
            f'User-Agent: {user_agent}\r\n'
            f'{auth_header}'
            'Accept: application/json, text/plain;q=0.9, */*;q=0.1\r\n'
            'Accept-Encoding: identity\r\n'
            'Connection: close\r\n\r\n'
        ).encode('ascii')
        tls_socket.sendall(request)
        status, headers, body = _read_response(
            tls_socket,
            deadline=deadline,
            read_timeout=read_timeout,
            max_bytes=max_bytes,
        )
        if status in {408, 425, 429} or 500 <= status <= 599:
            raise ProviderTemporaryError('provider_http_temporary_failure')
        if status != 200:
            raise ProviderConfigurationError('provider_http_status_invalid')
        return ProviderHTTPResponse(headers=headers, body=body)
    except ProviderConfigurationError:
        raise
    except ProviderTemporaryError:
        raise
    except (OSError, ssl.SSLError, socket.timeout):
        raise ProviderTemporaryError('provider_fetch_failed') from None
    finally:
        if tls_socket is not None:
            tls_socket.close()
        elif raw_socket is not None:
            raw_socket.close()


def fetch_https(
    url: str,
    *,
    allowed_hosts: frozenset[str],
    user_agent: str = _DEFAULT_USER_AGENT,
    authorization: str = '',
    connect_timeout: float = 3,
    read_timeout: float = 5,
    deadline_seconds: float = 10,
    max_bytes: int = 1024 * 1024,
) -> ProviderHTTPResponse:
    if (
        not isinstance(allowed_hosts, frozenset)
        or not allowed_hosts
        or any(not valid_public_host(host) for host in allowed_hosts)
    ):
        raise ProviderConfigurationError('provider_source_hosts_invalid')
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 for value in (
        connect_timeout,
        read_timeout,
        deadline_seconds,
    )):
        raise ProviderConfigurationError('provider_timeout_invalid')
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 1 <= max_bytes <= 1024 * 1024:
        raise ProviderConfigurationError('provider_response_limit_invalid')
    parsed, host, port = _validated_url(url, frozenset(item.casefold() for item in allowed_hosts))
    user_agent = _validated_user_agent(user_agent)
    authorization = _validated_authorization(authorization)
    deadline = time.monotonic() + min(float(deadline_seconds), 60)
    destinations = _ordered_addresses(_resolve_public(host, deadline))
    target = parsed.path or '/'
    if parsed.query:
        target = f'{target}?{parsed.query}'
    for destination in destinations:
        try:
            return _fetch_from_address(
                destination=destination,
                host=host,
                port=port,
                target=target,
                user_agent=user_agent,
                authorization=authorization,
                deadline=deadline,
                connect_timeout=min(float(connect_timeout), 30),
                read_timeout=min(float(read_timeout), 30),
                max_bytes=max_bytes,
            )
        except ProviderTemporaryError:
            continue
    raise ProviderTemporaryError('provider_fetch_failed')
