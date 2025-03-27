"""Support for Boneco button."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback as AddConfigEntryEntitiesCallback,
)
from homeassistant.components.button import (
    ButtonEntity,
    ButtonEntityDescription,
)
from pyboneco import BonecoDeviceState

from .const import DEVICE_CLEAN_PERIOD, ISS_REPLACE_PERIOD, FILTER_REPLACE_PERIOD
from .coordinator import BonecoConfigEntry, BonecoDataUpdateCoordinator
from .entity import BonecoEntity, BonecoEntityDescription


@dataclass(kw_only=True)
class BonecoButtonEntityDescription(
    BonecoEntityDescription,
    ButtonEntityDescription,
):
    """Describes Battery Notes button entity."""

    set_value_fn: Callable[[BonecoDeviceState, datetime | None], None]
    next_value_shift: timedelta


BUTTONS: tuple[BonecoButtonEntityDescription, ...] = (
    BonecoButtonEntityDescription(
        key="reset_reminder_clean_date",
        translation_key="reset_reminder_clean_date",
        exists_fn=lambda data: data.state.has_reminder_clean_date,
        set_value_fn=lambda state, value: state.set_reminder_clean_date(value),
        next_value_shift=timedelta(days=DEVICE_CLEAN_PERIOD),
    ),
    BonecoButtonEntityDescription(
        key="reset_reminder_iss_date",
        translation_key="reset_reminder_iss_date",
        exists_fn=lambda data: data.state.has_reminder_iss_date,
        set_value_fn=lambda state, value: state.set_reminder_iss_date(value),
        next_value_shift=timedelta(days=ISS_REPLACE_PERIOD),
    ),
    BonecoButtonEntityDescription(
        key="reset_reminder_filter_date",
        translation_key="reset_reminder_filter_date",
        exists_fn=lambda data: data.state.has_reminder_filter_date,
        set_value_fn=lambda state, value: state.set_reminder_filter_date(value),
        next_value_shift=timedelta(days=FILTER_REPLACE_PERIOD),
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
        BonecoButton(coordinator, description)
        for description in BUTTONS
        if description.exists_fn(coordinator.data)
    )


class BonecoButton(BonecoEntity, ButtonEntity):
    """Representation of a Boneco button."""

    entity_description: BonecoButtonEntityDescription

    def __init__(
        self,
        coordinator: BonecoDataUpdateCoordinator,
        entity_description: BonecoButtonEntityDescription,
    ) -> None:
        """Initialize the Boneco sensor."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._attr_unique_id = (
            f"{coordinator.auth_data.address}-{entity_description.key}"
        )

    async def async_press(self) -> None:
        """Press the button."""
        await self.coordinator.update_state(
            lambda state: self.entity_description.set_value_fn(
                state,
                datetime.now() + self.entity_description.next_value_shift
                if not state.has_service_operating_counter
                else None,
            )
        )
