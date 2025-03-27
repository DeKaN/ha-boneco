"""Support for Boneco fan."""

from dataclasses import dataclass
import math
from typing import Any

from homeassistant.components.fan import (
    FanEntity,
    FanEntityDescription,
    FanEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback as AddConfigEntryEntitiesCallback,
)
from homeassistant.util.percentage import (
    percentage_to_ranged_value,
    ranged_value_to_percentage,
)
from homeassistant.util.scaling import int_states_in_range
from pyboneco import BonecoDeviceState

from .const import AIR_FAN_SPEED_RANGE, OTHER_FAN_SPEED_RANGE
from .coordinator import BonecoConfigEntry, BonecoDataUpdateCoordinator
from .entity import BonecoEntity, BonecoValueEntityDescription


@dataclass(kw_only=True)
class BonecoFanEntityDescription(
    BonecoValueEntityDescription[int], FanEntityDescription
):
    """Describes Boneco fan entity."""


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BonecoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Boneco fan based on a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            BonecoFan(
                coordinator,
                BonecoFanEntityDescription(
                    key="fan",
                    translation_key="fan",
                    value_fn=lambda data: data.state.fan_level,
                ),
            ),
        ]
    )


class BonecoFan(BonecoEntity, FanEntity):
    """Representation of a Boneco fan."""

    entity_description: BonecoFanEntityDescription

    def __init__(
        self,
        coordinator: BonecoDataUpdateCoordinator,
        entity_description: BonecoFanEntityDescription,
    ) -> None:
        """Initialize the Boneco fan."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._attr_unique_id = (
            f"{coordinator.auth_data.address}-{entity_description.key}"
        )
        self.speed_range = (
            AIR_FAN_SPEED_RANGE if self._is_air_fan() else OTHER_FAN_SPEED_RANGE
        )
        self._attr_speed_count = int_states_in_range(self.speed_range)
        self._attr_supported_features = FanEntityFeature.SET_SPEED
        if self._is_air_fan():
            self._attr_supported_features |= (
                FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
            )
            self._attr_name = None

    async def async_set_percentage(self, percentage: int) -> None:
        """Set fan speed percentage."""
        if percentage == 0 and self._is_air_fan():
            return await self.async_turn_off()
        await self.coordinator.update_state(
            lambda state: _update_percentage(
                state,
                math.ceil(percentage_to_ranged_value(self.speed_range, percentage)),
            )
        )

    @property
    def percentage(self) -> int | None:
        """Return the current speed as a percentage."""
        level = self.entity_description.value_fn(self.coordinator.data)
        return ranged_value_to_percentage(self.speed_range, level)

    @property
    def is_on(self) -> bool | None:
        """Return true if device is on (for fan devices only)."""
        return self.coordinator.data.state.is_enabled

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn the device on (for fan devices only)."""
        await self.coordinator.update_state(lambda state: _switch_device(state, True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the device off (for fan devices only)."""
        await self.coordinator.update_state(lambda state: _switch_device(state, False))

    def _is_air_fan(self) -> bool:
        return self.coordinator.data.state.is_air_fan


def _switch_device(state: BonecoDeviceState, value: bool) -> None:
    state.is_enabled = value


def _update_percentage(state: BonecoDeviceState, speed: int) -> None:
    state.fan_level = speed
