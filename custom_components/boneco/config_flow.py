"""Config flow for the Boneco integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any

from bleak.backends.device import BLEDevice
import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS, CONF_PASSWORD, CONF_SENSOR_TYPE
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.helpers.device_registry import format_mac
from pyboneco import (
    SUPPORTED_DEVICE_CLASSES_BY_MODEL,
    BonecoAdvertisingData,
    BonecoAuth,
    BonecoAuthState,
    BonecoClient,
)

from .const import DOMAIN, WAIT_FOR_CONFIRM_PAIRING_TIMEOUT, WAIT_FOR_PAIRING_TIMEOUT

_LOGGER = logging.getLogger(__name__)


def _name_from_discovery(discovery: DiscoveredBoneco) -> str:
    """Get the name from a discovery."""
    results = discovery.device.address.replace("-", ":").split(":")
    short_address = f"{results[-2].upper()}{results[-1].upper()}"[-4:]
    return f"{discovery.name} {short_address}"


def _parse_advertisement_data(
    manufacturer_data: dict[int, bytes],
) -> BonecoAdvertisingData | None:
    args = next(iter(manufacturer_data.items()), None)
    return BonecoAdvertisingData(*args) if args is not None else None


@dataclass
class DiscoveredBoneco:
    device: BLEDevice
    advertisement: BonecoAdvertisingData


class BonecoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Boneco."""

    VERSION = 1
    _discovered: DiscoveredBoneco = None
    _discovered_advs: dict[str, DiscoveredBoneco] = {}
    _auth_data: BonecoAuth = None
    _client: BonecoClient = None
    _pairing_task: asyncio.Task = None
    _confirm_task: asyncio.Task = None

    async def async_step_bluetooth(
        self, discovery_info: bluetooth.BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle the bluetooth discovery step."""
        _LOGGER.debug("Discovered bluetooth device: %s", discovery_info.as_dict())
        await self.async_set_unique_id(format_mac(discovery_info.address))
        self._abort_if_unique_id_configured()
        parsed_adverisement = _parse_advertisement_data(
            discovery_info.advertisement.manufacturer_data
        )
        if parsed_adverisement is None or not parsed_adverisement.is_boneco_device:
            _LOGGER.warning("Got not Boneco device: %s", discovery_info.as_dict())
            return self.async_abort(reason="not_supported")

        self.context["title_placeholders"] = {
            "name": discovery_info.name,
            "address": discovery_info.address,
        }

        self._discovered = DiscoveredBoneco(discovery_info.device, parsed_adverisement)
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovery."""
        assert self._discovered is not None

        if user_input is not None:
            return await self._async_choose_next_step()

        self._set_confirm_only()
        placeholders = {"name": self._discovered.device.name}
        return self.async_show_form(
            step_id="bluetooth_confirm", description_placeholders=placeholders
        )

    async def async_step_wait_for_pairing_mode(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle bluetooth pairing start step."""
        if not self._pairing_task:
            _LOGGER.debug("Initializing pairing task")
            self._pairing_task = self.hass.async_create_task(
                self._async_wait_for_pairing_mode(),
                eager_start=False,
            )

        if not self._pairing_task.done():
            _LOGGER.debug("Show progress for pairing task")
            return self.async_show_progress(
                step_id="wait_for_pairing_mode",
                progress_action="wait_for_pairing_mode",
                progress_task=self._pairing_task,
            )

        try:
            await self._pairing_task
            await self._client.connect()
            _LOGGER.debug("Pairing task completed, starting key exchange")
        except TimeoutError:
            _LOGGER.debug("Timeout for pairing task")
            return self.async_show_progress_done(next_step_id="pairing_timeout")
        finally:
            self._pairing_task = None

        return self.async_show_progress_done(next_step_id="wait_for_confirm_pairing")

    async def async_step_pairing_timeout(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Inform the user that the device never entered pairing mode."""
        if user_input is not None:
            _LOGGER.debug("Restarting pairing task")
            return await self.async_step_wait_for_pairing_mode()

        self._set_confirm_only()
        return self.async_show_form(step_id="pairing_timeout")

    async def async_step_wait_for_confirm_pairing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle bluetooth confirm pairing step."""
        if not self._confirm_task:
            _LOGGER.debug("Initializing confirm pairing task")
            self._confirm_task = self.hass.async_create_task(
                self._async_authorize(),
                eager_start=False,
            )

        if not self._confirm_task.done():
            _LOGGER.debug("Show progress for confirm pairing task")
            return self.async_show_progress(
                step_id="wait_for_confirm_pairing",
                progress_action="wait_for_confirm_pairing",
                progress_task=self._confirm_task,
            )

        try:
            await self._confirm_task
            _LOGGER.debug("Confirm pairing task completed")
        except TimeoutError:
            _LOGGER.debug("Timeout for confirm pairing task")
            return self.async_show_progress_done(next_step_id="confirm_pairing_timeout")
        finally:
            self._confirm_task = None
            await self._client.disconnect()

        return self.async_show_progress_done(next_step_id="confirm")

    async def async_step_confirm_pairing_timeout(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Inform the user that the device never confirmed pairing."""
        if user_input is not None:
            _LOGGER.debug("Restarting confirm pairing task")
            return await self.async_step_wait_for_pairing_mode()

        self._set_confirm_only()
        return self.async_show_form(step_id="confirm_pairing_timeout")

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm a single device."""
        data = self._auth_data.save()
        title = data["name"]
        if not data["key"]:
            _LOGGER.debug("Key not found for device %s", title)
            self.async_abort("invalid_auth")

        await self.async_set_unique_id(
            format_mac(self._auth_data.address), raise_on_progress=False
        )
        self._abort_if_unique_id_configured()

        _LOGGER.debug("Creating entry for device %s", title)
        return self.async_create_entry(
            title=title,
            data={
                CONF_ADDRESS: data["address"],
                CONF_PASSWORD: data["key"],
                CONF_SENSOR_TYPE: SUPPORTED_DEVICE_CLASSES_BY_MODEL[title],
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            _LOGGER.debug("User chosen device with address %s", address)
            self._discovered = self._discovered_advs[address]
            await self.async_set_unique_id(format_mac(address), raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return await self._async_choose_next_step()

        _LOGGER.debug("User initiated device discovery")
        self._async_discover_devices()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            address: _name_from_discovery(parsed)
                            for address, parsed in self._discovered_advs.items()
                        }
                    ),
                }
            ),
            errors=errors,
        )

    async def _async_choose_next_step(self) -> ConfigFlowResult:
        self._auth_data = BonecoAuth(self._discovered.device)
        self._client = BonecoClient(self._auth_data)

        if not self._discovered.advertisement.pairing_active:
            return await self.async_step_wait_for_pairing_mode()

        return await self.async_step_wait_for_confirm_pairing()

    async def _async_wait_for_pairing_mode(self) -> None:
        """Process advertisements until pairing mode is detected."""
        assert self._auth_data

        def is_device_in_pairing_mode(
            discovery_info: bluetooth.BluetoothServiceInfo,
        ) -> bool:
            adv_data = _parse_advertisement_data(discovery_info.manufacturer_data)
            return (
                adv_data is not None
                and adv_data.is_boneco_device
                and adv_data.pairing_active
            )

        await bluetooth.async_process_advertisements(
            self.hass,
            is_device_in_pairing_mode,
            {"address": self._auth_data.address},
            bluetooth.BluetoothScanningMode.ACTIVE,
            WAIT_FOR_PAIRING_TIMEOUT,
        )

    async def _async_authorize(self) -> None:
        assert self._client

        confirmed_event = asyncio.Event()

        def on_state_update(auth: BonecoAuth) -> None:
            _LOGGER.debug(
                "Got new auth state: current=%s, level=%d",
                auth.current_state,
                auth.current_auth_level,
            )
            if auth.current_state == BonecoAuthState.CONFIRMED:
                confirmed_event.set()

        self._auth_data.set_auth_state_callback(on_state_update)
        if not self._client.is_connected:
            await self._client.connect()
        async with asyncio.timeout(WAIT_FOR_CONFIRM_PAIRING_TIMEOUT):
            await self._client.authorize()
            await confirmed_event.wait()

    @callback
    def _async_discover_devices(self) -> None:
        current_addresses = self._async_current_ids(include_ignore=False)
        for discovery_info in bluetooth.async_discovered_service_info(
            self.hass, connectable=True
        ):
            address = discovery_info.address
            if (
                format_mac(address) in current_addresses
                or address in self._discovered_advs
            ):
                continue

            parsed_adverisement = _parse_advertisement_data(
                discovery_info.advertisement.manufacturer_data
            )
            if parsed_adverisement is None or not parsed_adverisement.is_boneco_device:
                continue

            self._discovered_advs[address] = DiscoveredBoneco(
                discovery_info.device, parsed_adverisement
            )

        if not self._discovered_advs:
            raise AbortFlow("no_devices_found")
