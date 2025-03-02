"""Support for Boneco binary sensors."""

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback as AddConfigEntryEntitiesCallback,
)
from pyboneco import BonecoDeviceClass

from .const import DEVICES_WITH_FILTER
from .coordinator import BonecoConfigEntry, BonecoDataUpdateCoordinator
from .entity import BonecoEntity, BonecoValueEntityDescription

PARALLEL_UPDATES = 0


@dataclass(kw_only=True)
class BonecoBinarySensorEntityDescription(
    BonecoValueEntityDescription[bool], BinarySensorEntityDescription
):
    """Describes Boneco binary sensor entity."""


BINARY_SENSORS: tuple[BonecoBinarySensorEntityDescription, ...] = (
    BonecoBinarySensorEntityDescription(
        key="fan_error",
        translation_key="fan_error",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.info.fan_error,
    ),
    BonecoBinarySensorEntityDescription(
        key="no_water",
        translation_key="no_water",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        exists_fn=lambda data: not data.state.is_air_fan,
        value_fn=lambda data: data.info.no_water,
    ),
    BonecoBinarySensorEntityDescription(
        key="hum_pack_error",
        translation_key="hum_pack_error",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        exists_fn=lambda data: not data.state.is_air_fan,
        value_fn=lambda data: data.info.hum_pack_error,
    ),
    BonecoBinarySensorEntityDescription(
        key="no_filter",
        translation_key="no_filter",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        exists_fn=lambda data: data.info.device.device_class in DEVICES_WITH_FILTER,
        value_fn=lambda data: data.info.no_filter,
    ),
    BonecoBinarySensorEntityDescription(
        key="no_front_cover",
        translation_key="no_front_cover",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        exists_fn=lambda data: data.info.device.device_class
        == BonecoDeviceClass.TOP_CLIMATE,
        value_fn=lambda data: data.info.front_cover_error,
    ),
    BonecoBinarySensorEntityDescription(
        key="change_water",
        translation_key="change_water",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        exists_fn=lambda data: data.info.device.device_class
        == BonecoDeviceClass.TOP_CLIMATE,
        value_fn=lambda data: data.state.is_change_water_needed,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BonecoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Boneco sensor based on a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        BonecoBinarySensor(coordinator, description)
        for description in BINARY_SENSORS
        if description.exists_fn(coordinator.data)
    )


class BonecoBinarySensor(BonecoEntity, BinarySensorEntity):
    """Representation of a Boneco binary sensor."""

    entity_description: BonecoBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: BonecoDataUpdateCoordinator,
        entity_description: BonecoBinarySensorEntityDescription,
    ) -> None:
        """Initialize the Boneco sensor."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._attr_unique_id = (
            f"{coordinator.auth_data.address}-{entity_description.key}"
        )

    @property
    def is_on(self) -> bool:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(self.coordinator.data)
