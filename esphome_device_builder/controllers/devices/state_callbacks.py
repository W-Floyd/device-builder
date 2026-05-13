"""Per-attribute state-monitor callback mixin for ``DevicesController``."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ...helpers.mac_addresses import derive_interface_macs
from ...models import DeviceState, DeviceStateChangedData, EventType

if TYPE_CHECKING:
    from ...device_builder import DeviceBuilder
    from ...models import Device

_LOGGER = logging.getLogger(__name__)


class DeviceStateCallbacksMixin:
    """Per-attribute mDNS state callbacks for ``DevicesController``."""

    if TYPE_CHECKING:
        # Supplied by the host controller class.
        _db: DeviceBuilder

        def _devices_by_name(self, name: str) -> list[Device]: ...
        def _fire_device_updated(self, device: Device) -> None: ...
        async def _persist_device_ip_async(self, configuration: str, ip: str) -> None: ...
        async def _persist_device_metadata_async(
            self, configuration: str, **fields: object
        ) -> None: ...
        async def _persist_storage_version_async(
            self, configuration: str, version: str
        ) -> None: ...

    def _on_state_change(self, name: str, state: DeviceState, source: str) -> None:
        """Forward state monitor updates onto the event bus."""
        for device in self._devices_by_name(name):
            old_state = device.state
            device.state = state
            _LOGGER.info(
                "Device %s (%s): %s → %s (via %s)",
                name,
                device.configuration,
                old_state,
                state,
                source,
            )
            # Frontend's ``DeviceStateChangedEventData`` is the flat
            # ``{configuration, state}`` shape; sending the full
            # ``device`` object made the destructure resolve both
            # fields to ``undefined`` and the table never updated.
            self._db.bus.fire(
                EventType.DEVICE_STATE_CHANGED,
                DeviceStateChangedData(
                    configuration=device.configuration,
                    state=state.value,
                ),
            )

    def _on_ip_change(self, name: str, ip: str, addresses: list[str]) -> None:
        """
        Forward IP updates onto the event bus and persist the primary value.

        ``ip=""`` (with an empty *addresses* list) means the device
        dropped off mDNS; the last-known primary stays on disk so
        the OTA address cache survives the offline window. Only
        ``ip`` is persisted; ``addresses`` is the live mDNS view
        and gets repopulated by the next monitor pass.
        """
        new_addresses = list(addresses)
        for device in self._devices_by_name(name):
            if device.ip == ip and device.ip_addresses == new_addresses:
                continue
            ip_changed = device.ip != ip
            device.ip = ip
            device.ip_addresses = list(new_addresses)
            _LOGGER.debug(
                "Device %s (%s) IPs: %s",
                name,
                device.configuration,
                ", ".join(new_addresses) or "(cleared)",
            )
            if ip and ip_changed:
                self._db.create_background_task(
                    self._persist_device_ip_async(device.configuration, ip)
                )
            self._fire_device_updated(device)

    def _on_version_change(self, name: str, version: str) -> None:
        """Apply a fresh ESPHome version observed via mDNS."""
        for device in self._devices_by_name(name):
            if device.deployed_version == version:
                continue

            # StorageJSON.load/save are blocking; push to a background
            # task so any error gets surfaced via the loop's exception
            # handler.
            self._db.create_background_task(
                self._persist_storage_version_async(device.configuration, version)
            )

            old_version = device.deployed_version
            device.deployed_version = version
            device.update_available = bool(
                device.current_version and version != device.current_version
            )
            _LOGGER.info(
                "Device %s (%s) version: %s → %s (via mdns)",
                name,
                device.configuration,
                old_version or "?",
                version,
            )
            self._fire_device_updated(device)

    def _on_mac_address_change(self, name: str, mac: str) -> None:
        """
        Apply a MAC address observed via mDNS and derive interface MACs.

        The mDNS broadcast is always the device's primary MAC.
        When the YAML loads ``ethernet`` or any
        ``esp32_ble*`` / ``bluetooth_*`` integration the
        corresponding interface MAC is derived via
        :func:`derive_interface_macs`. Only the primary is
        persisted; derived MACs recompute on the next reload from
        primary + ``loaded_integrations``.
        """
        for device in self._devices_by_name(name):
            if device.mac_address == mac:
                continue
            device.mac_address = mac
            device.ethernet_mac, device.bluetooth_mac = derive_interface_macs(
                mac, device.target_platform, device.loaded_integrations
            )
            self._db.create_background_task(
                self._persist_device_metadata_async(device.configuration, mac_address=mac)
            )
            self._fire_device_updated(device)

    def _on_api_encryption_change(self, name: str, encryption: str) -> None:
        r"""
        Apply the API-encryption state observed via mDNS.

        Stores the broadcast value (or empty string for "TXT
        absent, device is plaintext") on the in-memory device.
        Also promotes ``api_encrypted`` to True when a truthy
        cipher arrives, since ESPHome's Jinja-templated
        ``packages`` (issue #437) can leave the scan-time YAML
        pass with ``api_encrypted=False`` for a fully-encrypted
        device. The empty-string broadcast deliberately doesn't
        clear ``api_encrypted``: wire-says-no with YAML-says-yes
        is the legitimate "mismatch" / "pending" shape the
        existing state machine handles.
        """
        for device in self._devices_by_name(name):
            wire_promotes_encrypted = bool(encryption) and not device.api_encrypted
            if device.api_encryption_active == encryption and not wire_promotes_encrypted:
                continue
            device.api_encryption_active = encryption
            if wire_promotes_encrypted:
                device.api_encrypted = True
            self._fire_device_updated(device)

    def _on_config_hash_change(self, name: str, config_hash: str) -> None:
        """
        Apply a running-firmware config hash observed via mDNS.

        Stores the hash on the in-memory device and, when both
        expected and deployed hashes are known, flips
        ``has_pending_changes`` to reflect the comparison.
        Devices on firmware that predates the ``config_hash`` TXT
        broadcast never trigger this callback and stay on the
        legacy mtime check.
        """
        for device in self._devices_by_name(name):
            if device.deployed_config_hash == config_hash:
                continue
            old_hash = device.deployed_config_hash
            device.deployed_config_hash = config_hash
            # Mtime side stays with the periodic scanner poll so this
            # callback can stay off-disk and non-blocking. A YAML
            # edit between polls (~5s) self-corrects on the next scan.
            if device.expected_config_hash:
                device.has_pending_changes = device.expected_config_hash != config_hash
            _LOGGER.info(
                "Device %s (%s) config_hash: %s → %s (via mdns)",
                name,
                device.configuration,
                old_hash or "?",
                config_hash,
            )
            self._fire_device_updated(device)
