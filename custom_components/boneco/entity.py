"""An abstract class common to all Boneco entities."""

from collections.abc import Callable
from typing import Any, Generic, TypeVar

from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pyboneco import BonecoDeviceState

from .coordinator import BonecoDataUpdateCoordinator
from .models import BonecoCombinedState

T = TypeVar("T")


class BonecoEntity(CoordinatorEntity[BonecoDataUpdateCoordinator]):
    """Generic entity encapsulating common attributes of Boneco device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BonecoDataUpdateCoordinator,
        context: Any = None,
    ) -> None:
        """Popuates common attributes."""
        super().__init__(coordinator, context)
        self._attr_device_info = coordinator.device_info


class BonecoEntityDescription(EntityDescription):
    """Generic Boneco entity description."""

    exists_fn: Callable[[BonecoCombinedState], bool] = lambda _: True


class BonecoValueEntityDescription(Generic[T], BonecoEntityDescription):
    """Generic Boneco entity with single read-only value description."""

    value_fn: Callable[[BonecoCombinedState], T]


class BonecoWritableValueEntityDescription(Generic[T], BonecoValueEntityDescription[T]):
    """Generic Boneco entity with single read-write value description."""

    set_value_fn: Callable[[BonecoDeviceState, T], None]
