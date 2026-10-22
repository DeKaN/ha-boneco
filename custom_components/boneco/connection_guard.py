"""One integration owns a Boneco address at a time, including setup and pending cleanup."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_ADDRESS
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN

LEASES_KEY = "_boneco_connection_owners"


def address_of(entry):
    return str(entry.data.get(CONF_ADDRESS, "")).replace("-", ":").upper()


def active_owner(hass, entry):
    address = address_of(entry)
    leases = hass.data.get(LEASES_KEY, {})
    owner = leases.get(address)
    if owner and owner != entry.entry_id:
        return hass.config_entries.async_get_entry(owner)
    # Also protect against an already-running original integration without leases.
    for other in hass.config_entries.async_entries(DOMAIN):
        if (
            other.entry_id != entry.entry_id
            and address_of(other) == address
            and other.state is ConfigEntryState.LOADED
        ):
            return other
    return None


def claim(hass, entry):
    """Synchronous claim before the first await prevents simultaneous setup."""
    if owner := active_owner(hass, entry):
        raise ConfigEntryNotReady(
            f"{owner.title} ({owner.domain}) already uses this Boneco device. "
            "Disable the other device entry before enabling this one."
        )
    leases = hass.data.setdefault(LEASES_KEY, {})
    address = address_of(entry)
    owner = leases.get(address)
    if owner and owner != entry.entry_id:
        raise ConfigEntryNotReady("Another Boneco entry still owns this device")
    leases[address] = entry.entry_id


def release(hass, entry):
    leases = hass.data.get(LEASES_KEY, {})
    address = address_of(entry)
    if leases.get(address) == entry.entry_id:
        leases.pop(address)
