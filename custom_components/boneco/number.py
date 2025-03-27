"""Support for Boneco numeric entities."""

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback as AddConfigEntryEntitiesCallback,
)
from pyboneco import MAX_LED_BRIGHTNESS, MIN_LED_BRIGHTNESS, BonecoDeviceState

from .coordinator import BonecoConfigEntry, BonecoDataUpdateCoordinator
from .entity import BonecoEntity, BonecoWritableValueEntityDescription
from .models import BonecoCombinedState


@dataclass(kw_only=True)
class BonecoNumberEntityDescription(
    BonecoWritableValueEntityDescription[float], NumberEntityDescription
):
    """Describes Boneco number entity."""

    min_value_fn: Callable[[BonecoCombinedState], int]
    max_value_fn: Callable[[BonecoCombinedState], int]


def _set_min_brightness(state: BonecoDeviceState, value: float) -> None:
    state.min_led_brightness = int(value)


def _set_max_brightness(state: BonecoDeviceState, value: float) -> None:
    state.max_led_brightness = int(value)


NUMBERS: tuple[BonecoNumberEntityDescription, ...] = (
    BonecoNumberEntityDescription(
        key="min_led_brightness",
        translation_key="min_led_brightness",
        native_unit_of_measurement="%",
        native_step=1,
        value_fn=lambda data: data.state.min_led_brightness,
        set_value_fn=_set_min_brightness,
        min_value_fn=lambda _: MIN_LED_BRIGHTNESS,
        max_value_fn=lambda data: data.state.max_led_brightness,
    ),
    BonecoNumberEntityDescription(
        key="max_led_brightness",
        translation_key="max_led_brightness",
        native_unit_of_measurement="%",
        native_step=1,
        value_fn=lambda data: data.state.max_led_brightness,
        set_value_fn=_set_max_brightness,
        min_value_fn=lambda data: data.state.min_led_brightness,
        max_value_fn=lambda _: MAX_LED_BRIGHTNESS,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BonecoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Boneco number based on a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        BonecoNumber(coordinator, description)
        for description in NUMBERS
        if description.exists_fn(coordinator.data)
    )


class BonecoNumber(BonecoEntity, NumberEntity):
    """Representation of a Boneco number."""

    entity_description: BonecoNumberEntityDescription

    def __init__(
        self,
        coordinator: BonecoDataUpdateCoordinator,
        entity_description: BonecoNumberEntityDescription,
    ) -> None:
        """Initialize the Boneco number."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._attr_unique_id = (
            f"{coordinator.auth_data.address}-{entity_description.key}"
        )

    @property
    def native_value(self) -> float:
        """Return the value reported by the number."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def native_min_value(self) -> float:
        """Return the minimum value."""
        return self.entity_description.min_value_fn(self.coordinator.data)

    @property
    def native_max_value(self) -> float:
        """Return the maximum value."""
        return self.entity_description.max_value_fn(self.coordinator.data)

    async def async_set_native_value(self, value: float) -> None:
        """Set new value."""
        await self.coordinator.update_state(
            lambda state: self.entity_description.set_value_fn(state, value)
        )
