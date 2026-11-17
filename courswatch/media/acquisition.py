"""Acquire bounded public CDN resources, without redirects or arbitrary URLs."""
from dataclasses import dataclass
from http.client import IncompleteRead
import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


ALLOWED_HOSTS = frozenset(('cdn.discordapp.com', 'media.discordapp.net',
                           'media.tenor.com', 'media.giphy.com', 'i.giphy.com'))


class MediaUnavailable(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise MediaUnavailable('redirect_not_allowed')


@dataclass(frozen=True)
class AcquiredMedia:
    data: bytes
    content_type: str


def validate_url(url):
    try:
        value = urlsplit(url)
        # No userinfo, ports, subdomain wildcards or encoded hostname variants.
        if (value.scheme != 'https' or value.hostname not in ALLOWED_HOSTS or
                value.username or value.password or value.port not in (None, 443) or value.fragment):
            raise MediaUnavailable('source_not_allowed')
    except (TypeError, ValueError):
        raise MediaUnavailable('source_not_allowed') from None
    return url


def fetch_media(descriptor, *, max_bytes=10 * 1024 * 1024, timeout=10, opener=None):
    """Return bounded bytes; errors contain only codes, never signed CDN URLs."""
    validate_url(descriptor.url)
    if descriptor.size_bytes is not None and descriptor.size_bytes > max_bytes:
        raise MediaUnavailable('source_too_large')
    request = urllib.request.Request(descriptor.url, headers={
        'User-Agent': 'UPBCourseWatch/1.0', 'Accept': 'image/png,image/jpeg,image/gif,image/webp'})
    client = opener or urllib.request.build_opener(_NoRedirect())
    started = time.monotonic()
    try:
        with client.open(request, timeout=timeout) as response:
            status = getattr(response, 'status', 200)
            if status is not None and status != 200:
                raise MediaUnavailable('source_http_' + str(status))
            raw_length = response.headers.get('Content-Length')
            length = int(raw_length) if raw_length is not None else None
            if length is not None and length < 0:
                raise MediaUnavailable('source_invalid_length')
            if length is not None and length > max_bytes:
                raise MediaUnavailable('source_too_large')
            content_type = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
            chunks = []
            total = 0
            while total <= max_bytes:
                if time.monotonic() - started > timeout:
                    raise MediaUnavailable('source_deadline')
                chunk = response.read(min(65536, max_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            data = b''.join(chunks)
    except MediaUnavailable:
        raise
    except urllib.error.HTTPError as error:
        code = 'source_expired' if error.code in (401, 403, 404) else 'source_http_' + str(error.code)
        raise MediaUnavailable(code) from None
    except IncompleteRead:
        raise MediaUnavailable('source_truncated') from None
    except (OSError, ValueError, socket.timeout):
        raise MediaUnavailable('source_unavailable') from None
    if len(data) > max_bytes:
        raise MediaUnavailable('source_too_large')
    if length is not None and len(data) != length:
        raise MediaUnavailable('source_truncated' if len(data) < length else 'source_invalid_length')
    if content_type and content_type not in ('image/png', 'image/jpeg', 'image/gif', 'image/webp', 'application/octet-stream'):
        raise MediaUnavailable('unsupported_content_type')
    return AcquiredMedia(data, content_type)
