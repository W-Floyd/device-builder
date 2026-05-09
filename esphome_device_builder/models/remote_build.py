"""Remote-build feature models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from mashumaro.mixins.orjson import DataClassORJSONMixin


class RemoteBuildPeerSource(StrEnum):
    """
    How a peer dashboard ended up in :meth:`list_hosts`.

    ``mdns``: discovered via the ``_esphomebuilder._tcp.local.``
    browse. ``manual``: added by the user via
    ``remote_build/add_manual_host`` for cross-subnet or
    non-multicast LANs where mDNS doesn't reach but L3 unicast
    does.
    """

    MDNS = "mdns"
    MANUAL = "manual"


@dataclass
class ManualHost(DataClassORJSONMixin):
    """
    A user-supplied peer entry stored in the metadata sidecar.

    Persisted under ``_remote_build.manual_hosts``; merged into
    :meth:`list_hosts` output as a :class:`RemoteBuildPeer` row
    with ``source=MANUAL`` and empty version fields. Phase 2b does
    no version / fingerprint resolution; phase 4 attempts the
    connection and fills the version fields in.
    """

    hostname: str
    port: int


@dataclass
class StoredToken(DataClassORJSONMixin):
    """
    A receiver-side issued bearer token, persisted by hash.

    Cleartext is the wire form ``{token_id}.{secret}``; only
    ``secret_sha256`` lands on disk. ``token_id`` is the lookup key
    (constant-time table hit), ``secret_sha256`` is what the
    middleware compares against the bearer's secret half via
    ``hmac.compare_digest``.

    ``bound_dashboard_id`` starts ``None`` and is filled in by the
    phase-3b3 first-use binding the first time an authenticated
    request arrives carrying a peer's ``X-Dashboard-ID``. After
    that, requests presenting the same token but a different
    dashboard_id are rejected as 403.
    """

    token_id: str
    label: str
    secret_sha256: str
    created_at: float
    bound_dashboard_id: str | None = None


@dataclass
class TokenSummary(DataClassORJSONMixin):
    """
    Public-facing token row for ``remote_build/list_tokens``.

    Mirrors :class:`StoredToken` but drops ``secret_sha256``: the
    stored hash isn't sensitive in the same way the cleartext is,
    but exposing it would let a network attacker who's already
    seen the on-disk metadata match candidate cleartext bearers
    against the wire shape, so the frontend has no business
    reading it.
    """

    token_id: str
    label: str
    created_at: float
    bound_dashboard_id: str | None = None


@dataclass
class StoredPairing(DataClassORJSONMixin):
    """
    Offloader-side persisted record of a paired build server.

    Identifies one remote dashboard this dashboard has paired with
    (= confirmed cert fingerprint out-of-band, then submitted a
    bearer the receiver accepted). Persisted in
    ``.device-builder.json`` under
    ``_remote_build.paired_remotes``.

    ``token_cleartext`` is the wire bearer (``{token_id}.{secret}``)
    needed to authenticate every request the offloader sends to
    this receiver. Unlike receiver-side tokens (stored as
    ``secret_sha256`` because the receiver only verifies), the
    offloader needs the cleartext to *present* the bearer, so a
    one-way hash is the wrong primitive. Phase 4a stores cleartext;
    encryption-at-rest is a follow-up (single keyfile under
    ``<data_dir>/storage`` with 0o600 perms; same lifecycle as the
    cert key).

    ``pin_sha256`` is the SPKI fingerprint observed during pairing
    and confirmed by the user out-of-band. The peer-link layer
    (phase 5) will assert this on every TLS handshake; a mismatch
    triggers the re-auth wizard (phase 8). ``dashboard_id`` is
    what we send in ``X-Dashboard-ID``; the receiver pins it after
    first use.

    ``server_version`` / ``esphome_version`` are captured at pair
    time so the offload scheduler (phase 7) can match builds to
    version-compatible peers without a round-trip.
    """

    hostname: str
    port: int
    label: str
    pin_sha256: str
    token_cleartext: str
    dashboard_id: str
    server_version: str
    esphome_version: str
    paired_at: float


@dataclass
class PairingSummary(DataClassORJSONMixin):
    """
    Public-facing wire view of :class:`StoredPairing`.

    Returned from ``remote_build/list_pool`` and
    ``remote_build/confirm_pair``. Drops ``token_cleartext``: the
    offloader has the bearer in storage, the frontend has no
    business reading it back. Same projection logic as
    :class:`StoredToken` → :class:`TokenSummary` on the
    receiver side.
    """

    hostname: str
    port: int
    label: str
    pin_sha256: str
    dashboard_id: str
    server_version: str
    esphome_version: str
    paired_at: float


@dataclass
class PairingPreview(DataClassORJSONMixin):
    """
    Wire shape returned from ``remote_build/preview_pair``.

    The single load-bearing field is ``pin_sha256`` — the SPKI
    fingerprint observed during the TLS handshake to the
    candidate receiver. The frontend renders it for OOB
    verification ("does this match the fingerprint shown on the
    receiver's Build server settings page?"); on confirm, the
    user's "yes" gates the persistence step.

    Versions / dashboard_id intentionally NOT in the preview:
    those would require either an authenticated round-trip
    (we don't have the bearer yet) or a new unauth endpoint on
    the receiver (scope creep for 4a). The frontend already has
    versions from mDNS or the manual-host entry.
    """

    pin_sha256: str


@dataclass
class RemoteBuildSettings(DataClassORJSONMixin):
    """
    Receiver-side settings for the remote-build feature (storage shape).

    Stored in ``.device-builder.json`` under the ``_remote_build``
    top-level key. ``tokens`` carries :class:`StoredToken` rows
    *with* the ``secret_sha256`` hash; this is the on-disk /
    in-process shape only and MUST NOT be serialised over the
    wire. Use :class:`RemoteBuildSettingsView` (or the
    ``_summarise_token`` projection) for any response that leaves
    the server.

    ``paired_remotes`` carries the offloader-side pairing records
    from phase 4a; same shape concern (token_cleartext is on-disk
    only, never on the wire) as ``tokens``.
    """

    enabled: bool = False
    manual_hosts: list[ManualHost] = field(default_factory=list)
    tokens: list[StoredToken] = field(default_factory=list)
    paired_remotes: list[StoredPairing] = field(default_factory=list)


@dataclass
class RemoteBuildSettingsView(DataClassORJSONMixin):
    """
    Wire view of :class:`RemoteBuildSettings`.

    Returned from every WS command that exposes settings to a
    client. Identical to :class:`RemoteBuildSettings` except:

    - ``tokens`` is a list of :class:`TokenSummary` (no
      ``secret_sha256``) so issuing or removing tokens via the
      CRUD methods can't leak the stored hash back to the
      frontend through the response shape.
    - ``paired_remotes`` is a list of :class:`PairingSummary`
      (no ``token_cleartext``) so the offloader's stored bearers
      can't leak via the same path.
    """

    enabled: bool = False
    manual_hosts: list[ManualHost] = field(default_factory=list)
    tokens: list[TokenSummary] = field(default_factory=list)
    paired_remotes: list[PairingSummary] = field(default_factory=list)


@dataclass
class RemoteBuildPeer(DataClassORJSONMixin):
    """
    A peer dashboard known to this dashboard.

    Wire shape returned from ``remote_build/list_hosts``. Two
    sources land in the same row shape:

    * ``source=MDNS``: discovered via the
      ``_esphomebuilder._tcp.local.`` browse. ``name`` is the
      mDNS service-instance name (leftmost label, e.g.
      ``desktop``); ``hostname`` is the SRV target (e.g.
      ``desktop.local.``); ``addresses`` is the parsed A / AAAA
      list with IPv6 scope preserved; versions come from TXT.
    * ``source=MANUAL``: user-supplied via
      ``remote_build/add_manual_host``. ``name`` is the full
      hostname verbatim (NOT the leftmost label) so an IP-only
      entry like ``192.168.1.10`` reads sensibly in the UI rather
      than truncating to ``"192"``. ``hostname`` is the same
      user-entered string, ``port`` is the user-entered port,
      ``addresses`` is empty, and version fields are blank until
      phase 4 attempts the connection.

    Phase 2 stops at discovery + manual entry; pairing / connection
    / fingerprint pinning lands in later phases.
    """

    name: str
    hostname: str
    port: int
    source: RemoteBuildPeerSource
    addresses: list[str] = field(default_factory=list)
    server_version: str = ""
    esphome_version: str = ""


@dataclass
class IdentityView(DataClassORJSONMixin):
    """
    Receiver-side dashboard identity, projected for the Settings UI.

    Returned from ``remote_build/get_identity`` and
    ``remote_build/rotate_identity``. The cert + key PEMs are
    intentionally NOT included: only the ``pin_sha256`` (the
    SHA-256 of the cert's SubjectPublicKeyInfo, lowercase hex) is
    safe to ship, and the cert PEM itself adds nothing the
    fingerprint doesn't already let an offloader pin against.

    ``server_version`` is this dashboard's package version;
    ``esphome_version`` is the bundled esphome's. Both are also
    advertised in mDNS TXT (see :class:`DashboardAdvertiser`),
    but the Settings UI doesn't browse mDNS to render its own
    "Build host" card — surfacing them here keeps the card a
    single WS call.

    ``listener_bound`` reports whether the
    ``/remote-build/v1/*`` HTTPS receiver site is currently
    serving traffic on this dashboard. Lets the Settings UI
    distinguish "rotation succeeded AND the listener is back
    up" from "rotation succeeded but the rebuild fail-softed"
    (port now bound by something else, cert load throws, …).
    The latter is silent in the logs without this flag.
    """

    dashboard_id: str
    pin_sha256: str
    server_version: str
    esphome_version: str
    listener_bound: bool = False
