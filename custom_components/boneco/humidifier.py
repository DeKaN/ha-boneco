"""Support for Boneco humidifier."""

from dataclasses import dataclass
from typing import Any

from homeassistant.components.humidifier import (
    MODE_AUTO,
    MODE_BABY,
    MODE_NORMAL,
    MODE_SLEEP,
    HumidifierDeviceClass,
    HumidifierEntity,
    HumidifierEntityDescription,
    HumidifierEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback as AddConfigEntryEntitiesCallback,
)
from pyboneco import (
    MAX_HUMIDITY,
    MIN_HUMIDITY,
    BonecoModeStatus,
    BonecoOperationMode,
    BonecoOperationModeConfig,
)

from .coordinator import BonecoConfigEntry, BonecoDataUpdateCoordinator
from .entity import BonecoEntity, BonecoEntityDescription
from .models import BonecoCombinedState

BONECO_MODE_MAPPING: dict[BonecoModeStatus, str] = {
    BonecoModeStatus.CUSTOM: MODE_NORMAL,
    BonecoModeStatus.AUTO: MODE_AUTO,
    BonecoModeStatus.BABY: MODE_BABY,
    BonecoModeStatus.SLEEP: MODE_SLEEP,
}
BONECO_MODE_REVERSE_MAPPING: dict[str, BonecoModeStatus] = {
    ha_mode: boneco_mode for boneco_mode, ha_mode in BONECO_MODE_MAPPING.items()
}


@dataclass(kw_only=True)
class BonecoHumidifierEntityDescription(
    BonecoEntityDescription, HumidifierEntityDescription
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
            BonecoHumidifier(
                coordinator,
                BonecoHumidifierEntityDescription(
                    key="humidifier",
                    translation_key="humidifier",
                    name=None,
                    exists_fn=lambda data: _get_humidifier_operating_modes(data)
                    is not None,
                ),
            ),
        ]
    )


class BonecoHumidifier(BonecoEntity, HumidifierEntity):
    """Representation of a Boneco humidifier."""

    _attr_device_class = HumidifierDeviceClass.HUMIDIFIER
    _attr_max_humidity = MAX_HUMIDITY
    _attr_min_humidity = MIN_HUMIDITY
    entity_description: BonecoHumidifierEntityDescription

    def __init__(
        self,
        coordinator: BonecoDataUpdateCoordinator,
        entity_description: BonecoHumidifierEntityDescription,
    ) -> None:
        """Initialize the Boneco humidifier."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._attr_unique_id = (
            f"{coordinator.auth_data.address}-{entity_description.key}"
        )
        modes = _get_humidifier_operating_modes(coordinator.data)
        if modes is not None:
            modes = [
                BONECO_MODE_MAPPING[mode]
                for mode, supported in modes.items()
                if supported
            ]
            self._attr_supported_features = HumidifierEntityFeature.MODES
        self._attr_available_modes = modes

    @property
    def is_on(self) -> bool | None:
        """Return true if device is on."""
        return self.coordinator.data.state.is_enabled

    @property
    def current_humidity(self) -> int:
        """Return the current humidity."""
        return self.coordinator.data.info.humidity

    @property
    def target_humidity(self) -> float | None:
        """Return the humidity we try to reach."""
        return self.coordinator.data.state.target_humidity

    @property
    def mode(self) -> str:
        """Return the current mode, e.g., home, auto, baby."""
        return BONECO_MODE_MAPPING[self.coordinator.data.state.mode_status]

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the device on."""
        state = self.coordinator.data.state
        state.is_enabled = True
        await self.set_state(state)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the device off."""
        state = self.coordinator.data.state
        state.is_enabled = False
        await self.set_state(state)

    async def async_set_humidity(self, humidity: int) -> None:
        """Set new target humidity."""
        state = self.coordinator.data.state
        state.target_humidity = humidity
        await self.set_state(state)

    async def async_set_mode(self, mode: str) -> None:
        """Set new target preset mode."""
        state = self.coordinator.data.state
        state.mode_status = BONECO_MODE_REVERSE_MAPPING[mode]
        await self.set_state(state)


def _get_humidifier_operating_modes(
    data: BonecoCombinedState,
) -> BonecoOperationModeConfig:
    return data.info.supported_operating_modes[BonecoOperationMode.HUMIDIFIER]
