"""Support for Boneco select."""

from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback as AddConfigEntryEntitiesCallback,
)
from pyboneco import BonecoDeviceState, BonecoOperationMode

from .coordinator import BonecoConfigEntry, BonecoDataUpdateCoordinator
from .entity import BonecoEntity, BonecoWritableValueEntityDescription
from .models import BonecoCombinedState


@dataclass(kw_only=True)
class BonecoSelectEntityDescription(
    BonecoWritableValueEntityDescription[int], SelectEntityDescription
):
    """Describes Boneco select entity."""


def _update_operating_mode(state: BonecoDeviceState, value: int) -> None:
    state.operating_mode = BonecoOperationMode(value)


SELECTS: tuple[BonecoSelectEntityDescription, ...] = (
    BonecoSelectEntityDescription(
        key="operating_mode",
        translation_key="operating_mode",
        exists_fn=lambda data: _has_several_operating_modes(data),
        value_fn=lambda data: data.state.operating_mode,
        set_value_fn=_update_operating_mode,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BonecoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Boneco select based on a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        BonecoSelect(coordinator, description)
        for description in SELECTS
        if description.exists_fn(coordinator.data)
    )


class BonecoSelect(BonecoEntity, SelectEntity):
    """Representation of a Boneco select."""

    entity_description: BonecoSelectEntityDescription

    def __init__(
        self,
        coordinator: BonecoDataUpdateCoordinator,
        entity_description: BonecoSelectEntityDescription,
    ) -> None:
        """Initialize the Boneco select."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._attr_unique_id = (
            f"{coordinator.auth_data.address}-{entity_description.key}"
        )
        self._attr_options = _get_operating_modes(self.coordinator.data)

    @property
    def current_option(self) -> str | None:
        """Return the selected entity option to represent the entity state."""
        return str(self.entity_description.value_fn(self.coordinator.data))

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        await self.coordinator.update_state(
            lambda state: self.entity_description.set_value_fn(state, int(option))
        )


def _get_operating_modes(
    data: BonecoCombinedState,
) -> list[str]:
    return [
        str(k) for k, v in data.info.device.operating_modes.items() if v is not None
    ]


def _has_several_operating_modes(
    data: BonecoCombinedState,
) -> bool:
    return len(_get_operating_modes(data)) > 1
