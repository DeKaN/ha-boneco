"""Provides Boneco DataUpdateCoordinator."""

import asyncio
from collections.abc import Callable
from datetime import timedelta
import logging

from bleak_retry_connector import close_stale_connections_by_address

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
    REQUEST_REFRESH_DEFAULT_COOLDOWN,
)
from pyboneco import BonecoAuth, BonecoClient, BonecoDeviceClass, BonecoDeviceState

from .const import DOMAIN, MANUFACTURER, UPDATE_INTERVAL, UPDATE_TIMEOUT
from .models import BonecoCombinedState

_LOGGER = logging.getLogger(__name__)

type BonecoConfigEntry = ConfigEntry[BonecoDataUpdateCoordinator]


class BonecoDataUpdateCoordinator(DataUpdateCoordinator[BonecoCombinedState]):
    """Boneco device update coordinator."""

    _lock: asyncio.Lock = asyncio.Lock()
    _pending_state: BonecoDeviceState = None
    device_info: dr.DeviceInfo = None

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: BonecoConfigEntry,
        boneco_auth: BonecoAuth,
        device_class: BonecoDeviceClass,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            logger=_LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
            update_method=self._async_fetch_state,
            always_update=True,
        )
        self.auth_data = boneco_auth
        self.device_class = device_class
        self._client = BonecoClient(self.auth_data)
        self._debounced_write: Debouncer = Debouncer(
            hass,
            logger=_LOGGER,
            cooldown=REQUEST_REFRESH_DEFAULT_COOLDOWN,
            immediate=False,
            function=self._async_set_state,
            background=True,
        )

    async def set_state(self, new_state: BonecoDeviceState) -> None:
        """Overwrite state for the device."""
        _LOGGER.debug(
            "Saving a new state %s instead of %s",
            vars(new_state),
            vars(self._last_state()),
        )
        self._pending_state = new_state
        self._debounced_write.async_schedule_call()

    async def update_state(
        self, update_fn: Callable[[BonecoDeviceState], None]
    ) -> None:
        """Update state for the device"""
        _LOGGER.debug("Updating state")
        new_state = self._last_state()
        update_fn(new_state)
        await self.set_state(new_state)

    async def async_shutdown(self) -> None:
        self._debounced_write.async_shutdown()
        await super().async_shutdown()

    def _last_state(self) -> BonecoDeviceState:
        return self._pending_state or self.data.state

    async def _async_setup(self):
        address = self.auth_data.address
        await close_stale_connections_by_address(address)

    async def _async_set_state(self):
        try:
            _LOGGER.debug("Sending new state = %s", vars(self._pending_state))
            async with self._lock:
                await self._client.connect()
                await self._client.set_state(self._pending_state)
            self._pending_state = None
            self._debounced_refresh.async_schedule_call()
        except Exception as e:
            _LOGGER.warning("Can't update device state. %s", e, exc_info=True)
        finally:
            _LOGGER.debug("Another operation has started = %s", self._lock.locked())
            if not self._lock.locked():
                await self._client.disconnect()

    async def _async_fetch_state(self) -> BonecoCombinedState:
        try:
            async with self._lock, asyncio.timeout(UPDATE_TIMEOUT):
                await self._client.connect()
                name = await self._client.get_device_name()
                info = await self._client.get_device_info()
                state = await self._client.get_state()
                _LOGGER.debug(
                    "Fetched device name='%s', device info='%s', device state='%s'",
                    name,
                    vars(info),
                    vars(state),
                )
                if self.device_info is None:
                    self.device_info = dr.DeviceInfo(
                        identifiers={(DOMAIN, self.auth_data.address)},
                        connections={(dr.CONNECTION_BLUETOOTH, self.auth_data.address)},
                        manufacturer=MANUFACTURER,
                        model=self.auth_data.name,
                        name=name,
                        serial_number=info.serial_number,
                        sw_version=info.software_version,
                        hw_version=info.hardware_version,
                    )
                return BonecoCombinedState(name, info, state)
        except Exception as err:
            raise UpdateFailed(f"Unable to fetch data: {err}") from err
        finally:
            _LOGGER.debug("Another operation has started = %s", self._lock.locked())
            if not self._lock.locked():
                await self._client.disconnect()
