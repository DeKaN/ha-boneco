"""Support for Boneco switch."""

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback as AddConfigEntryEntitiesCallback,
)
from pyboneco import BonecoDeviceState

from .coordinator import BonecoConfigEntry, BonecoDataUpdateCoordinator
from .entity import BonecoEntity, BonecoWritableValueEntityDescription


@dataclass(kw_only=True)
class BonecoSwitchEntityDescription(
    BonecoWritableValueEntityDescription[bool], SwitchEntityDescription
):
    """Describes Boneco switch entity."""


def _update_child_lock(state: BonecoDeviceState, value: bool) -> None:
    state.is_locked = value


def _update_always_history_active(state: BonecoDeviceState, value: bool) -> None:
    state.is_locked = value


SWITCHES: tuple[BonecoSwitchEntityDescription, ...] = (
    BonecoSwitchEntityDescription(
        key="child_lock",
        translation_key="child_lock",
        entity_category=EntityCategory.CONFIG,
        value_fn=lambda data: data.state.is_locked,
        set_value_fn=_update_child_lock,
    ),
    BonecoSwitchEntityDescription(
        key="history_active",
        translation_key="history_active",
        entity_category=EntityCategory.CONFIG,
        exists_fn=lambda data: data.info.device.history_support,
        value_fn=lambda data: data.state.is_always_history_active,
        set_value_fn=_update_always_history_active,
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
        BonecoSwitch(coordinator, description)
        for description in SWITCHES
        if description.exists_fn(coordinator.data)
    )


class BonecoSwitch(BonecoEntity, SwitchEntity):
    """Representation of a Boneco switch."""

    entity_description: BonecoSwitchEntityDescription

    def __init__(
        self,
        coordinator: BonecoDataUpdateCoordinator,
        entity_description: BonecoSwitchEntityDescription,
    ) -> None:
        """Initialize the Boneco switch."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._attr_unique_id = (
            f"{coordinator.auth_data.address}-{entity_description.key}"
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if entity is on."""
        return self.entity_description.value_fn(self.coordinator.data)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        await self.coordinator.update_state(
            lambda state: self.entity_description.set_value_fn(state, True)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the entity off."""
        await self.coordinator.update_state(
            lambda state: self.entity_description.set_value_fn(state, False)
        )
