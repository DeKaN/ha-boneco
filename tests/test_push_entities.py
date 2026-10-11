"""Import the production sensor module; replace only HA entity base classes."""

import importlib
import sys
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from types import ModuleType, SimpleNamespace

import pytest
from test_sessions import INFO, STATE


@pytest.fixture
def sensor_module(integration, monkeypatch):
    def register(name, **attrs):
        module = ModuleType(name)
        vars(module).update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    class DeviceClass(StrEnum):
        TEMPERATURE = "temperature"
        DATE = "date"
        VOLATILE_ORGANIC_COMPOUNDS = "voc"
        SIGNAL_STRENGTH = "signal_strength"

    class BonecoEntity:
        def __init__(self, coordinator):
            self.coordinator = coordinator

    @dataclass(kw_only=True)
    class ValueDescription:
        key: str
        value_fn: object
        exists_fn: object = lambda _: True
        translation_key: str | None = None
        native_unit_of_measurement: str | None = None
        device_class: str | None = None
        state_class: str | None = None
        icon: str | None = None
        entity_category: str | None = None
        entity_registry_enabled_default: bool = True

        def __class_getitem__(cls, item):
            return cls

    register("homeassistant.core", HomeAssistant=object)
    register("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    register("homeassistant.helpers.typing", StateType=object)
    register(
        "homeassistant.components.sensor",
        SensorEntity=type("SensorEntity", (), {}),
        SensorEntityDescription=type("SensorEntityDescription", (), {}),
        SensorDeviceClass=DeviceClass,
        SensorStateClass=SimpleNamespace(MEASUREMENT="measurement"),
    )
    const = sys.modules["homeassistant.const"]
    for key, val in {
        "EntityCategory": SimpleNamespace(DIAGNOSTIC="diagnostic"),
        "UnitOfTemperature": SimpleNamespace(CELSIUS="°C"),
        "SIGNAL_STRENGTH_DECIBELS_MILLIWATT": "dBm",
    }.items():
        monkeypatch.setattr(const, key, val, raising=False)
    register(
        "test_boneco.entity",
        BonecoEntity=BonecoEntity,
        BonecoValueEntityDescription=ValueDescription,
    )
    name = "test_boneco.sensor"
    monkeypatch.delitem(sys.modules, name, raising=False)
    return importlib.import_module(name)


def coordinator(integration):
    mod = integration["boneco"].coordinator
    return SimpleNamespace(
        data=SimpleNamespace(
            info=mod.BonecoDeviceInfo(INFO), state=mod.BonecoDeviceState("H700", STATE)
        ),
        auth_data=SimpleNamespace(address="AA:BB:CC:DD:EE:FF"),
    )


def test_temperature_zero_still_creates_entity(sensor_module, integration):
    co = coordinator(integration)
    co.data.info._temp = 0
    desc = next(d for d in sensor_module.SENSORS if d.key == "temperature")
    assert desc.exists_fn(co.data)
    assert sensor_module.BonecoSensor(co, desc).native_value == 0


@pytest.mark.parametrize("kind", ["filter", "iss", "clean"])
def test_service_sensor_is_a_date_not_a_changing_timestamp(
    sensor_module, integration, kind
):
    co = coordinator(integration)
    desc = next(d for d in sensor_module.SENSORS if d.key == f"reminder_{kind}_date")
    value = sensor_module.BonecoSensor(co, desc).native_value
    assert isinstance(value, date)
    assert not isinstance(value, datetime)


def test_air_quality_has_no_pm25_units_or_device_class(sensor_module, integration):
    co = coordinator(integration)
    desc = next(d for d in sensor_module.SENSORS if d.key == "pm25")
    assert desc.native_unit_of_measurement is None
    assert desc.device_class is None
    assert sensor_module.BonecoSensor(co, desc).native_value == 1


def test_absent_particle_data_is_unknown_and_raw_zero_is_valid(
    sensor_module, integration
):
    co = coordinator(integration)
    desc = next(d for d in sensor_module.SENSORS if d.key == "particle_raw")
    sensor = sensor_module.BonecoSensor(co, desc)
    cls = integration["boneco"].coordinator.BonecoDeviceInfo
    for raw, expected in [(65535, None), (0, 0)]:
        packet = bytearray(INFO)
        packet[4:6] = raw.to_bytes(2, "little")
        co.data.info = cls(packet)
        assert sensor.native_value == expected
