"""Support for Boneco sensors."""

from dataclasses import dataclass
from datetime import datetime

from homeassistant.components import bluetooth
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback as AddConfigEntryEntitiesCallback,
)
from homeassistant.helpers.typing import StateType

from .const import DEVICE_CLEAN_PERIOD, FILTER_REPLACE_PERIOD, ISS_REPLACE_PERIOD
from .coordinator import BonecoConfigEntry, BonecoDataUpdateCoordinator
from .entity import BonecoEntity, BonecoValueEntityDescription

PARALLEL_UPDATES = 0


@dataclass(kw_only=True)
class BonecoSensorEntityDescription(
    BonecoValueEntityDescription[StateType], SensorEntityDescription
):
    """Describes Boneco sensor entity."""


SENSORS: tuple[BonecoSensorEntityDescription, ...] = (
    BonecoSensorEntityDescription(
        key="temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.TEMPERATURE,
        exists_fn=lambda data: data.info.temperature is not None,
        value_fn=lambda data: data.info.temperature,
    ),
    BonecoSensorEntityDescription(
        key="pm25",
        # Keep the unique ID so existing dashboards/automations retain the entity.
        # This is an ordinal air quality level, not a PM2.5 concentration.
        translation_key="air_quality_level",
        icon="mdi:air-filter",
        exists_fn=lambda data: data.info.has_particle_sensor,
        value_fn=lambda data: data.info.particle_value,
    ),
    BonecoSensorEntityDescription(
        key="particle_raw",
        translation_key="particle_raw",
        icon="mdi:blur",
        entity_category=EntityCategory.DIAGNOSTIC,
        exists_fn=lambda data: data.info.has_particle_sensor,
        value_fn=lambda data: data.info.particle_raw,
    ),
    BonecoSensorEntityDescription(
        key="voc",
        native_unit_of_measurement="µg/m³",
        state_class=SensorStateClass.MEASUREMENT,
        device_class=SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS,
        exists_fn=lambda data: data.info.has_particle_sensor,
        value_fn=lambda data: data.info.voc,
    ),
    BonecoSensorEntityDescription(
        key="reminder_filter_date",
        translation_key="reminder_filter_date",
        device_class=SensorDeviceClass.DATE,
        exists_fn=lambda data: data.state.has_reminder_filter_date,
        value_fn=lambda data: data.state.get_reminder_filter_date(
            FILTER_REPLACE_PERIOD
        ),
    ),
    BonecoSensorEntityDescription(
        key="reminder_iss_date",
        translation_key="reminder_iss_date",
        device_class=SensorDeviceClass.DATE,
        exists_fn=lambda data: data.state.has_reminder_iss_date,
        value_fn=lambda data: data.state.get_reminder_iss_date(ISS_REPLACE_PERIOD),
    ),
    BonecoSensorEntityDescription(
        key="reminder_clean_date",
        translation_key="reminder_clean_date",
        device_class=SensorDeviceClass.DATE,
        exists_fn=lambda data: data.state.has_reminder_clean_date,
        value_fn=lambda data: data.state.get_reminder_clean_date(DEVICE_CLEAN_PERIOD),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BonecoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Boneco sensor based on a config entry."""
    coordinator = entry.runtime_data
    entities = [
        BonecoSensor(coordinator, description)
        for description in SENSORS
        if description.exists_fn(coordinator.data)
    ]
    entities.append(
        BonecoRSSISensor(
            coordinator,
            BonecoSensorEntityDescription(
                key="rssi",
                translation_key="bluetooth_signal",
                native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
                device_class=SensorDeviceClass.SIGNAL_STRENGTH,
                state_class=SensorStateClass.MEASUREMENT,
                entity_registry_enabled_default=False,
                entity_category=EntityCategory.DIAGNOSTIC,
                value_fn=lambda _: None,
            ),
        )
    )
    for key in ("connection_count", "notification_count"):
        entities.append(
            BonecoConnectionSensor(
                coordinator,
                BonecoSensorEntityDescription(
                    key=key,
                    translation_key=key,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    icon="mdi:bluetooth-connect",
                    value_fn=lambda _: None,
                ),
            )
        )
    async_add_entities(entities)


class BonecoSensor(BonecoEntity, SensorEntity):
    """Representation of a Boneco sensor."""

    entity_description: BonecoSensorEntityDescription

    def __init__(
        self,
        coordinator: BonecoDataUpdateCoordinator,
        entity_description: BonecoSensorEntityDescription,
    ) -> None:
        """Initialize the Boneco sensor."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._attr_unique_id = (
            f"{coordinator.auth_data.address}-{entity_description.key}"
        )

    @property
    def native_value(self) -> str | int | None:
        """Return the state of the sensor."""
        value = self.entity_description.value_fn(self.coordinator.data)
        # DATE entities must not change with every packet's current second.
        if (
            self.entity_description.device_class == SensorDeviceClass.DATE
            and isinstance(value, datetime)
        ):
            return value.date()
        return value


class BonecoRSSISensor(BonecoSensor):
    """Representation of a Boneco RSSI sensor."""

    @property
    def native_value(self) -> str | int | None:
        """Return the state of the sensor."""
        if service_info := bluetooth.async_last_service_info(
            self.hass, self.coordinator.auth_data.address
        ):
            return service_info.rssi
        return None


class BonecoConnectionSensor(BonecoSensor):
    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> int:
        if self.entity_description.key == "connection_count":
            return self.coordinator.connections
        return self.coordinator.notifications
