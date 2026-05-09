"""
Offloader-side TLS plumbing for the pair / confirm flow.

The pair flow (phase 4) is intentionally two-step at the WS
surface:

1. ``preview_pair`` — open a TLS connection to the candidate
   receiver, observe the cert's SubjectPublicKeyInfo
   fingerprint, return it. No persistence, no bearer presented.
   The frontend renders the fingerprint for the user to compare
   against what the receiver's Build server settings page
   shows; that out-of-band match is the only thing standing
   between a legitimate pair and a LAN MITM at first contact.

2. ``confirm_pair`` — re-handshake (defends against a TOCTOU
   between the preview and the confirm), assert the new
   handshake's pin matches the user-confirmed value, present
   the user-pasted bearer against ``GET /remote-build/v1/health``
   with ``X-Dashboard-ID`` set, and persist the
   :class:`StoredPairing` only when both succeed.

This module is the TLS / HTTPS half of those two commands. The
controller wraps them, owns the storage transaction, and surfaces
typed errors. We deliberately don't trust the OS cert store here
— receivers ship self-signed certs, the only trust we want is
SPKI pinning. ``ssl.CERT_NONE`` lets the handshake complete; we
extract the cert ourselves from the binary form.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import ssl
from dataclasses import dataclass

import aiohttp
from cryptography import x509

from .dashboard_identity import compute_spki_fingerprint

_LOGGER = logging.getLogger(__name__)

# Receiver's HTTPS health route. Phase 3b2 lit this as the canary
# that proves the auth pipeline works end-to-end; we reuse it for
# bearer verification at pair-confirm time.
_HEALTH_PATH = "/remote-build/v1/health"

# Per-attempt timeout for the TLS handshake + health round-trip.
# A pair attempt against an unreachable host should fail fast so
# the UI can render a typed error rather than spinning; legitimate
# LAN handshakes complete in well under a second.
_PAIRING_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class PairingHealthResult:
    """
    Outcome of the pair-confirm round-trip.

    ``ok=True`` on a 2xx; ``http_status`` is set on any non-2xx
    response so the controller can pick the right error code
    (401 → ``UNAUTHORIZED``, 403 → ``UNAUTHORIZED`` again with a
    "wrong dashboard_id" surface, 429 → ``RATE_LIMITED``, etc.).
    ``ok=False`` with ``http_status=None`` means we couldn't
    connect at all — the pin assertion failed, the handshake
    timed out, or the network refused us. The controller maps
    that to ``UNAVAILABLE`` (or ``PRECONDITION_FAILED`` for the
    pin-mismatch shape, which the helper raises directly via
    :exc:`PinMismatchError`).
    """

    ok: bool
    http_status: int | None = None


class PinMismatchError(Exception):
    """The handshake's observed pin didn't match the expected value."""

    def __init__(self, expected: str, observed: str) -> None:
        super().__init__(f"pin_sha256 mismatch: expected {expected!r}, observed {observed!r}")
        self.expected = expected
        self.observed = observed


async def observe_remote_pin(host: str, port: int) -> str:
    """
    Open a TLS connection to *host:port* and return the SPKI fingerprint.

    Used by ``preview_pair`` — no bearer is presented, no body is
    sent, we just complete the handshake and read the peer's cert.
    Returns the SHA-256 of the SubjectPublicKeyInfo as lowercase
    hex (matches the receiver's ``pin_sha256`` representation
    everywhere else in the codebase).

    Raises :exc:`OSError` (or a subclass like
    ``ConnectionRefusedError`` / ``asyncio.TimeoutError``) on the
    connect / handshake failure paths; the controller maps those
    to ``UNAVAILABLE``. We deliberately do NOT validate the cert's
    chain / hostname / expiry here — the pair flow's whole point
    is to trust this fingerprint going forward, but at preview
    time we haven't trusted anything yet.
    """
    cert_der = await asyncio.wait_for(_fetch_cert_der(host, port), timeout=_PAIRING_TIMEOUT_SECONDS)
    return _spki_fingerprint_from_der(cert_der)


async def verify_bearer(
    *,
    host: str,
    port: int,
    expected_pin: str,
    token_cleartext: str,
    dashboard_id: str,
) -> PairingHealthResult:
    """
    Verify a bearer + pin against the candidate receiver's ``/health``.

    Used by ``confirm_pair`` after the user OOB-confirmed the
    fingerprint preview. Re-handshakes (the second handshake's
    pin is asserted against *expected_pin* — :exc:`PinMismatchError`
    on divergence, defending against a TOCTOU between preview and
    confirm), then issues an authenticated GET. A 2xx is the
    "everything checks out, persist the pairing" signal; any
    non-2xx returns its status code so the controller can map it
    to a typed error.

    *dashboard_id* is the offloader's stable id (from
    :func:`helpers.dashboard_identity.get_or_create_identity`).
    Sent as ``X-Dashboard-ID``; the receiver's first-use
    binding (phase 3b3) records it. Subsequent peer-link
    requests will need to carry the same value.
    """
    ssl_ctx = _trust_no_ca_context()
    async with aiohttp.TCPConnector(
        ssl=ssl_ctx, force_close=True, enable_cleanup_closed=True
    ) as connector:
        url = f"https://{_format_host(host)}:{port}{_HEALTH_PATH}"
        headers = {
            "Authorization": f"Bearer {token_cleartext}",
            "X-Dashboard-ID": dashboard_id,
        }
        timeout = aiohttp.ClientTimeout(total=_PAIRING_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            try:
                async with session.get(url, headers=headers) as resp:
                    # The handshake landed; read the peer cert from
                    # the underlying transport so we can pin-assert.
                    observed = _observed_pin_from_response(resp)
                    if observed != expected_pin:
                        raise PinMismatchError(expected_pin, observed)
                    if 200 <= resp.status < 300:
                        return PairingHealthResult(ok=True, http_status=resp.status)
                    return PairingHealthResult(ok=False, http_status=resp.status)
            except aiohttp.ClientConnectorError as exc:
                _LOGGER.debug("Pairing connect failure to %s:%s: %s", host, port, exc)
                return PairingHealthResult(ok=False, http_status=None)
            except TimeoutError:
                _LOGGER.debug("Pairing timeout to %s:%s", host, port)
                return PairingHealthResult(ok=False, http_status=None)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


@functools.cache
def _trust_no_ca_context() -> ssl.SSLContext:
    """
    SSLContext that completes a handshake without validating chain or hostname.

    The pair flow's whole premise is that the receiver ships a
    self-signed cert and we pin its fingerprint OOB. Validating
    against the OS trust store would reject that cert outright;
    enabling hostname check would reject any IP-only or
    ``foo.local`` URL. We disable both, then assert the pin
    ourselves against what the user confirmed.

    Don't lift this context for any non-pair-flow use — it's
    deliberately permissive. Real peer-link traffic (phase 5)
    will use a per-pin verification callback instead of this
    no-trust shortcut.

    Cached at first call: ``ssl.create_default_context`` does a
    synchronous read of the OS trust store, and the configured
    SSLContext is stateless across pair attempts.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def _fetch_cert_der(host: str, port: int) -> bytes:
    """
    Open a TLS connection and return the peer's cert in DER.

    Uses ``asyncio.open_connection`` rather than aiohttp because
    we don't need the HTTP layer for a simple SPKI read — we just
    want the handshake to land. ``transport.get_extra_info("ssl_object")``
    gives us the live SSL object whose ``getpeercert(binary_form=True)``
    returns the cert in the format ``cryptography.x509`` parses.
    """
    ssl_ctx = _trust_no_ca_context()
    # ``asyncio.open_connection`` returns ``(reader, writer)``;
    # we only need the writer's transport to read the peer cert.
    _reader, writer = await asyncio.open_connection(host, port, ssl=ssl_ctx)
    try:
        ssl_obj = writer.get_extra_info("ssl_object")
        if ssl_obj is None:
            raise OSError("TLS handshake did not produce an ssl_object")
        cert_der = ssl_obj.getpeercert(binary_form=True)
        if not cert_der:
            raise OSError("TLS peer presented no certificate")
        return cert_der
    finally:
        writer.close()
        # Best-effort cleanup; a transport-level error during
        # close shouldn't mask the cert we already have in hand.
        # NOTE: CancelledError is *not* suppressed — if our caller
        # is cancelled while we're awaiting wait_closed, the
        # cancellation must propagate. ``suppress(CancelledError)``
        # silently breaks task cancellation.
        with contextlib.suppress(OSError):
            await writer.wait_closed()


def _observed_pin_from_response(resp: aiohttp.ClientResponse) -> str:
    """
    Pull the peer cert out of the live aiohttp response transport.

    aiohttp's ``response.connection.transport`` exposes the
    underlying asyncio transport; ``ssl_object.getpeercert(True)``
    on it returns the same DER we'd get from a manual handshake.
    Used at confirm time so the pin assertion happens against the
    same TLS connection the bearer health check landed on.

    NOTE: ``resp.connection.transport`` is documented in aiohttp's
    dev guide but isn't part of the stable public API surface; a
    major aiohttp upgrade could rename or remove it. The fallback
    in that case is to drop back to a separate ``open_connection``
    handshake the same way :func:`_fetch_cert_der` does, paying a
    second handshake at confirm time. Worth knowing if this surface
    breaks under a future bump.
    """
    conn = resp.connection
    if conn is None or conn.transport is None:
        raise OSError("response has no live transport to read cert from")
    ssl_obj = conn.transport.get_extra_info("ssl_object")
    if ssl_obj is None:
        raise OSError("response transport has no ssl_object")
    cert_der = ssl_obj.getpeercert(binary_form=True)
    if not cert_der:
        raise OSError("response transport peer presented no certificate")
    return _spki_fingerprint_from_der(cert_der)


def _spki_fingerprint_from_der(cert_der: bytes) -> str:
    """DER-input wrapper around :func:`compute_spki_fingerprint`."""
    return compute_spki_fingerprint(x509.load_der_x509_certificate(cert_der))


def _format_host(host: str) -> str:
    """Wrap raw IPv6 in brackets so URL parsing doesn't choke."""
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host
