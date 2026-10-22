from types import SimpleNamespace

import pytest
from conftest import ConfigEntryState


def hass_for(entries):
    return SimpleNamespace(
        data={},
        config_entries=SimpleNamespace(
            async_entries=lambda domain: [e for e in entries if e.domain == domain],
            async_get_entry=lambda key: next(
                (e for e in entries if e.entry_id == key), None
            ),
        ),
    )


def test_two_integration_cannot_claim_same_device_during_setup(
    integration, entry_factory
):
    first = entry_factory("first", "boneco")
    second = entry_factory("second", "boneco")
    hass = hass_for([first, second])
    guard = integration["boneco"].guard
    guard.claim(hass, first)
    with pytest.raises(RuntimeError):
        guard.claim(hass, second)
    guard.release(hass, first)
    guard.claim(hass, second)


def test_existing_loaded_original_is_protected_without_a_lease(
    integration, entry_factory
):
    first = entry_factory("first", "boneco")
    second = entry_factory("second", "boneco")
    first.state = ConfigEntryState.LOADED
    hass = hass_for([first, second])
    with pytest.raises(RuntimeError, match="Disable"):
        integration["boneco"].guard.claim(hass, second)


def test_different_devices_can_use_different_integration(integration, entry_factory):
    first = entry_factory("first", "boneco", "AA:BB:CC:DD:EE:01")
    second = entry_factory("second", "boneco", "AA:BB:CC:DD:EE:02")
    hass = hass_for([first, second])
    guard = integration["boneco"].guard
    guard.claim(hass, first)
    guard.claim(hass, second)


def test_wrong_owner_cannot_release_another_integration_lease(
    integration, entry_factory
):
    first = entry_factory("first", "boneco")
    second = entry_factory("second", "boneco")
    hass = hass_for([first, second])
    guard = integration["boneco"].guard
    guard.claim(hass, first)
    guard.release(hass, second)
    with pytest.raises(RuntimeError):
        guard.claim(hass, second)
