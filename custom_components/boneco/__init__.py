"""Set up Boneco and retain device ownership until cleanup has finished."""

from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.const import (
    CONF_ADDRESS,
    CONF_PASSWORD,
    CONF_SENSOR_TYPE,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from pyboneco import BonecoAuth, BonecoDeviceClass

from .connection_guard import claim, release
from .const import PLATFORMS_BY_TYPE
from .coordinator import BonecoConfigEntry, BonecoDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)
_CLEANUP_KEY = "_boneco_pending_cleanup"


def _retain_cleanup(hass, entry, coordinator):
    """Keep a strong reference and the device reservation until cleanup succeeds."""
    task = coordinator.start_shutdown()
    pending = hass.data.setdefault(_CLEANUP_KEY, {})
    if pending.get(entry.entry_id) is task:
        return
    pending[entry.entry_id] = task

    def finished(done):
        if done.cancelled() or done.exception() is not None:
            _LOGGER.error(
                "Boneco cleanup did not finish successfully; device remains reserved"
            )
            return
        if pending.get(entry.entry_id) is done:
            pending.pop(entry.entry_id)
            release(hass, entry)

    task.add_done_callback(finished)


async def async_setup_entry(hass: HomeAssistant, entry: BonecoConfigEntry) -> bool:
    if entry.entry_id in hass.data.get(_CLEANUP_KEY, {}):
        raise ConfigEntryNotReady("Previous Boneco Bluetooth cleanup is still pending")
    claim(hass, entry)
    coordinator = None
    try:
        address = entry.data[CONF_ADDRESS]
        device = bluetooth.async_ble_device_from_address(hass, address.upper())
        if device is None:
            raise ConfigEntryNotReady(f"Could not find Boneco device {address}")
        device_class = BonecoDeviceClass(entry.data[CONF_SENSOR_TYPE])
        auth = BonecoAuth(device, entry.data[CONF_PASSWORD])
        coordinator = entry.runtime_data = BonecoDataUpdateCoordinator(
            hass, entry, auth, device_class
        )

        async def on_stop(_event):
            _retain_cleanup(hass, entry, coordinator)
            await coordinator.async_shutdown()

        entry.async_on_unload(
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, on_stop)
        )
        await coordinator.async_config_entry_first_refresh()
        entry.async_on_unload(entry.add_update_listener(_async_update_listener))
        await hass.config_entries.async_forward_entry_setups(
            entry, PLATFORMS_BY_TYPE[device_class]
        )
        return True
    except BaseException:
        if coordinator is None:
            release(hass, entry)
        else:
            _retain_cleanup(hass, entry, coordinator)
            try:
                await coordinator.async_shutdown()
            except Exception as err:  # noqa: BLE001 - preserve setup failure
                _LOGGER.warning("Boneco cleanup after failed setup: %s", err)
        raise


async def _async_update_listener(hass: HomeAssistant, entry: BonecoConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: BonecoConfigEntry) -> bool:
    device_class = BonecoDeviceClass(entry.data[CONF_SENSOR_TYPE])
    unloaded = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS_BY_TYPE[device_class]
    )
    if not unloaded:
        return False
    _retain_cleanup(hass, entry, entry.runtime_data)
    await entry.runtime_data.async_shutdown()
    # A stopped entry can unload while retained cleanup still owns the device.
    # New setup waits in ConfigEntryNotReady until that lease is released.
    # Returning False here would put HA in non-recoverable FAILED_UNLOAD.
    return True
