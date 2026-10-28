"""Safety net: make any attempt to reach a non-loopback host from botocore raise, even from a client built without an endpoint."""

from __future__ import annotations

import contextlib
import ipaddress
from urllib.parse import urlparse

from botocore.httpsession import URLLib3Session


class RealAwsBlocked(RuntimeError):
    """A test tried to send an AWS request to a non-local host."""


def is_loopback(url: str) -> bool:
    host = urlparse(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@contextlib.contextmanager
def forbid_real_aws():
    original = URLLib3Session.send

    def guarded(self, request):
        if not is_loopback(request.url):
            raise RealAwsBlocked(
                f"refusing to send a request to {urlparse(request.url).hostname!r}: tests may only talk to the local mock"
            )
        return original(self, request)

    URLLib3Session.send = guarded
    try:
        yield
    finally:
        URLLib3Session.send = original
