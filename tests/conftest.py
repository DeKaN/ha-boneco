"""Small HA API fakes; transport and packet decoding use the real library code."""

import asyncio
import importlib
import sys
import types
from enum import Enum
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name, **attrs):
    item = types.ModuleType(name)
    item.__dict__.update(attrs)
    sys.modules[name] = item
    return item


class Platform(str, Enum):
    BINARY_SENSOR = "binary_sensor"
    FAN = "fan"
    SENSOR = "sensor"
    BUTTON = "button"
    HUMIDIFIER = "humidifier"
    NUMBER = "number"
    SELECT = "select"
    SWITCH = "switch"


class ConfigEntryState(Enum):
    LOADED = 1
    NOT_LOADED = 2
    SETUP_IN_PROGRESS = 3


class DataCoordinator:
    def __class_getitem__(cls, _item):
        return cls

    def __init__(self, hass, *, config_entry, update_method, **kwargs):
        self.hass = hass
        self.config_entry = config_entry
        self.update_method = update_method
        self.data = None
        self.last_update_success = True
        self.updates = 0

    def async_update_listeners(self):
        self.updates += 1

    def async_set_updated_data(self, data):
        self.data = data
        self.last_update_success = True
        self.async_update_listeners()

    def async_set_update_error(self, _error):
        self.last_update_success = False
        self.async_update_listeners()

    async def async_refresh(self):
        try:
            self.data = await self.update_method()
            self.last_update_success = True
        except Exception:  # noqa: BLE001 - emulate HA's coordinator error handling
            self.last_update_success = False

    async def async_shutdown(self):
        pass


class Debouncer:
    def __init__(self, *args, **kwargs):
        self.calls = 0
        self.closed = False

    def async_schedule_call(self):
        self.calls += 1

    def async_shutdown(self):
        self.closed = True


class ConfigFlow:
    def __init_subclass__(cls, **kwargs):
        pass

    def _async_current_ids(self, **kwargs):
        return self.current_ids

    async def async_set_unique_id(self, uid, **kwargs):
        self.unique_id = uid

    def _abort_if_unique_id_configured(self):
        assert self.unique_id not in self.current_ids

    def async_abort(self, **kwargs):
        return {"type": "abort", **kwargs}

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}


module("homeassistant")
module("homeassistant.core", HomeAssistant=object, callback=lambda fn: fn)
module("homeassistant.data_entry_flow", AbortFlow=RuntimeError)
module(
    "homeassistant.const",
    Platform=Platform,
    CONF_ADDRESS="address",
    CONF_PASSWORD="password",
    CONF_SENSOR_TYPE="sensor_type",
)
module(
    "homeassistant.config_entries",
    ConfigEntry=DataCoordinator,
    ConfigEntryState=ConfigEntryState,
    ConfigFlow=ConfigFlow,
    ConfigFlowResult=dict,
)
module("homeassistant.exceptions", ConfigEntryNotReady=RuntimeError)
module("homeassistant.helpers")
module(
    "homeassistant.helpers.device_registry",
    DeviceInfo=dict,
    CONNECTION_BLUETOOTH="bluetooth",
    format_mac=lambda a: a.upper(),
)
module("homeassistant.helpers.debounce", Debouncer=Debouncer)
module(
    "homeassistant.helpers.update_coordinator",
    DataUpdateCoordinator=DataCoordinator,
    UpdateFailed=RuntimeError,
)
module("homeassistant.components")
module(
    "homeassistant.components.bluetooth",
    async_ble_device_from_address=lambda h, a: h.device,
)


@pytest.fixture(scope="session")
def integration():
    name = "test_boneco"
    package = module(name)
    package.__path__ = [str(ROOT / "custom_components/boneco")]
    return {
        "boneco": types.SimpleNamespace(
            session=importlib.import_module(name + ".session"),
            coordinator=importlib.import_module(name + ".coordinator"),
            guard=importlib.import_module(name + ".connection_guard"),
            flow=importlib.import_module(name + ".config_flow"),
        )
    }


class Entry:
    def __init__(self, entry_id="test", domain="boneco", address="AA:BB:CC:DD:EE:FF"):
        self.entry_id = entry_id
        self.domain = domain
        self.title = "H700"
        self.state = ConfigEntryState.NOT_LOADED
        self.data = {"address": address, "password": "01" * 16, "sensor_type": 3}

    def async_create_background_task(self, hass, coro, **kwargs):
        return asyncio.create_task(coro)


@pytest.fixture
def entry_factory():
    return Entry
