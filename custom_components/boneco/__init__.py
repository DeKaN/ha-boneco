"""The Boneco integration."""

from __future__ import annotations

from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD, CONF_SENSOR_TYPE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from pyboneco import BonecoAuth, BonecoDeviceClass

from .const import PLATFORMS_BY_TYPE
from .coordinator import BonecoConfigEntry, BonecoDataUpdateCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: BonecoConfigEntry) -> bool:
    """Set up Boneco from a config entry."""
    assert entry.unique_id is not None

    address = entry.data[CONF_ADDRESS]
    device_key = entry.data[CONF_PASSWORD]
    device_class = BonecoDeviceClass(entry.data[CONF_SENSOR_TYPE])

    ble_device = bluetooth.async_ble_device_from_address(hass, address.upper())

    if not ble_device:
        raise ConfigEntryNotReady(
            f"Could not find Boneco device with address {address}"
        )
    auth_data = BonecoAuth(ble_device, device_key)
    coordinator = entry.runtime_data = BonecoDataUpdateCoordinator(
        hass,
        entry,
        auth_data,
        device_class,
    )
    await coordinator.async_config_entry_first_refresh()
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(
        entry, PLATFORMS_BY_TYPE[device_class]
    )

    return True


async def _async_update_listener(hass: HomeAssistant, entry: BonecoConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: BonecoConfigEntry) -> bool:
    """Unload a config entry."""
    sensor_type = BonecoDeviceClass(entry.data[CONF_SENSOR_TYPE])
    return await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS_BY_TYPE[sensor_type]
    )
