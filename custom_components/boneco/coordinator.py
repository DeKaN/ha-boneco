"""Coordinate periodic polls or notifications with an independent watchdog."""

import asyncio
import copy
import dataclasses
import logging
from collections.abc import Callable
from datetime import timedelta

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pyboneco import BonecoDeviceInfo, BonecoDeviceState

from .const import (
    CONF_CONNECTION_MODE,
    CONNECTION_MODE_PERSISTENT,
    CONNECTION_MODE_POLLING,
    DOMAIN,
    MANUFACTURER,
    PACKET_INFO,
    PACKET_STATE,
    REQUEST_WRITE_DEFAULT_COOLDOWN,
    UPDATE_INTERVAL,
)
from .models import BonecoCombinedState
from .session import PersistentSession

_LOGGER = logging.getLogger(__name__)
WATCHDOG_INTERVAL = 300
SHUTDOWN_TIMEOUT = 10

type BonecoConfigEntry = ConfigEntry[BonecoDataUpdateCoordinator]


@dataclasses.dataclass(eq=False, frozen=True)
class PendingUpdate:
    apply: Callable
    retry_safe: bool


class BonecoDataUpdateCoordinator(DataUpdateCoordinator[BonecoCombinedState]):
    def __init__(self, hass, config_entry, boneco_auth, device_class):
        self._persistent = (
            config_entry.options.get(CONF_CONNECTION_MODE, CONNECTION_MODE_POLLING)
            == CONNECTION_MODE_PERSISTENT
        )
        super().__init__(
            hass,
            logger=_LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_method=self._async_fetch_state,
            # Push uses a separate timer: incoming packets must not postpone checks.
            update_interval=None
            if self._persistent
            else timedelta(seconds=UPDATE_INTERVAL),
            always_update=True,
        )
        self.auth_data = boneco_auth
        self.device_class = device_class
        self.device_info = None
        self._pending_state = None
        self._pending_updates = []
        self._started = False
        self._stopped = False
        self._shutdown_task = None
        self._packets = {}
        self._last_packets = {}
        self._packet_handle = None
        self._retry_handle = None
        self._retry_task = None
        self._watchdog_handle = None
        self._watchdog_task = None
        self._retry_delay = 2
        self._published_notifications = 0
        self._session = PersistentSession(
            lambda: bluetooth.async_ble_device_from_address(
                hass, boneco_auth.address.upper()
            ),
            boneco_auth.save()["key"],
            on_packet=self._queue_packet,
            on_disconnect=self._disconnected,
            persistent=self._persistent,
        )
        self._debounced_write = Debouncer(
            hass,
            logger=_LOGGER,
            cooldown=REQUEST_WRITE_DEFAULT_COOLDOWN,
            immediate=False,
            function=self._async_set_state,
            background=True,
        )

    @property
    def connections(self):
        return self._session.connections

    @property
    def notifications(self):
        return self._session.notifications

    def _last_state(self):
        return self._pending_state or self.data.state

    async def update_state(self, update_fn, *, retry_safe=True):
        if self._stopped:
            raise RuntimeError("This Boneco entry is disabled")
        state = copy.copy(self._last_state())
        update_fn(state)
        self._pending_updates.append(PendingUpdate(update_fn, retry_safe))
        self._pending_state = state
        self.data = dataclasses.replace(self.data, state=state)
        self.async_update_listeners()
        self._debounced_write.async_schedule_call()

    @staticmethod
    def _apply_updates(state, updates):
        state = copy.copy(state)
        for update in updates:
            update.apply(state)
        return state

    def _discard_updates(self, updates):
        self._pending_updates = [u for u in self._pending_updates if u not in updates]

    def _merge_pending(self, state):
        self._pending_state = (
            self._apply_updates(state, self._pending_updates)
            if self._pending_updates
            else None
        )
        return self._pending_state or state

    def _queue_packet(self, kind, packet):
        if self._stopped or not self._persistent:
            return
        self._packets[kind] = packet
        if self._packet_handle is None:
            self._packet_handle = self.hass.loop.call_later(0.25, self._flush_packets)

    def _flush_packets(self):
        self._packet_handle = None
        if self._stopped or not self._started or self.data is None:
            self._packets.clear()
            return
        if self._session.busy:
            self._packet_handle = self.hass.loop.call_later(0.25, self._flush_packets)
            return
        if not self._session.connected:
            self._packets.clear()
            return
        data = self.data
        changed = False
        for kind, packet in self._packets.items():
            if packet == self._last_packets.get(kind):
                continue
            try:
                if kind == PACKET_INFO:
                    data = dataclasses.replace(data, info=BonecoDeviceInfo(packet))
                    changed = True
                elif kind == PACKET_STATE:
                    data = dataclasses.replace(
                        data,
                        state=self._merge_pending(
                            BonecoDeviceState(self._session.model_name, packet)
                        ),
                    )
                    changed = True
                else:
                    continue
            except (IndexError, KeyError, ValueError) as err:
                _LOGGER.warning("Ignoring malformed Boneco %s update: %s", kind, err)
                continue
            self._last_packets[kind] = packet
        self._packets.clear()
        if changed:
            self.async_set_updated_data(data)
        elif self.notifications != self._published_notifications:
            # Notify diagnostic entities even when sensor payloads are unchanged.
            self.async_update_listeners()
        self._published_notifications = self.notifications

    def _disconnected(self):
        self._packets.clear()
        self._last_packets.clear()
        if self._stopped or not self._started:
            return
        self.async_set_update_error(UpdateFailed("Boneco Bluetooth connection lost"))
        self._schedule_retry()

    def _schedule_retry(self):
        if (
            not self._persistent
            or self._stopped
            or not self._started
            or self._retry_handle is not None
            or (self._retry_task is not None and not self._retry_task.done())
        ):
            return
        self._retry_handle = self.hass.loop.call_later(
            self._retry_delay, self._start_retry
        )
        self._retry_delay = min(60, self._retry_delay * 2)

    def _start_retry(self):
        self._retry_handle = None
        if not self._stopped:
            self._retry_task = self.config_entry.async_create_background_task(
                self.hass, self._recover(), name=f"{DOMAIN} reconnect"
            )

    async def _recover(self):
        try:
            await self.async_refresh()
        finally:
            self._retry_task = None
            if not self._stopped and not self.last_update_success:
                self._schedule_retry()

    def _schedule_watchdog(self):
        if self._persistent and not self._stopped and self._watchdog_handle is None:
            self._watchdog_handle = self.hass.loop.call_later(
                WATCHDOG_INTERVAL, self._start_watchdog
            )

    def _start_watchdog(self):
        self._watchdog_handle = None
        if not self._stopped:
            self._watchdog_task = self.config_entry.async_create_background_task(
                self.hass, self._watchdog(), name=f"{DOMAIN} connection check"
            )

    async def _watchdog(self):
        try:
            await self.async_refresh()
        finally:
            self._watchdog_task = None
            self._schedule_watchdog()

    async def _async_fetch_state(self):
        try:
            name, info, state = await self._session.read()
        except Exception as err:
            self._schedule_retry()
            raise UpdateFailed(
                f"Boneco read failed: {type(err).__name__}: {err}"
            ) from err
        if self._stopped:
            raise asyncio.CancelledError
        # The session reconciles notifications arriving during each field read.
        self._packets.clear()
        self._last_packets.clear()
        if self.device_info is None:
            self.device_info = dr.DeviceInfo(
                identifiers={(DOMAIN, self.auth_data.address)},
                connections={(dr.CONNECTION_BLUETOOTH, self.auth_data.address)},
                manufacturer=MANUFACTURER,
                model=self._session.model_name,
                name=name,
                serial_number=str(info.serial_number),
                sw_version=info.software_version,
                hw_version=info.hardware_version,
            )
        self._started = True
        self._retry_delay = 2
        if self._retry_handle is not None:
            self._retry_handle.cancel()
            self._retry_handle = None
        self._schedule_watchdog()
        if self._pending_state is not None:
            self._debounced_write.async_schedule_call()
            state = self._merge_pending(state)
        return BonecoCombinedState(name=name, info=info, state=state)

    async def _async_set_state(self):
        updates = self._pending_updates[:]
        if not updates or self._stopped:
            return
        try:
            confirmed = await self._session.write(
                lambda state: self._apply_updates(state, updates),
                # BLE cannot guarantee exactly-once delivery after a lost reply.
                # Never replay a service reset at a much later reconnection.
                on_write_attempt=lambda: self._discard_updates(
                    [u for u in updates if not u.retry_safe]
                ),
            )
        except Exception as err:  # noqa: BLE001 - expose transport failure to HA
            if self._stopped:
                return
            if any(
                not u.retry_safe and u not in self._pending_updates for u in updates
            ):
                _LOGGER.warning(
                    "Service reset outcome is uncertain after BLE failure; "
                    "it will not be retried automatically"
                )
            if not self._pending_updates:
                self._pending_state = None
            self.async_set_update_error(UpdateFailed(f"Boneco write failed: {err}"))
            self._schedule_retry()
            return
        if self._stopped:
            return
        self._packets.pop(PACKET_STATE, None)
        self._last_packets.pop(PACKET_STATE, None)
        self._discard_updates(updates)
        self.async_set_updated_data(
            dataclasses.replace(self.data, state=self._merge_pending(confirmed))
        )
        if self._pending_updates:
            # Older HA debouncers ignore scheduling while their job is running.
            self.hass.loop.call_later(0, self._debounced_write.async_schedule_call)

    def start_shutdown(self):
        """Start exactly one cleanup, also shared by concurrent unload requests."""
        if self._shutdown_task is not None:
            return self._shutdown_task
        self._stopped = True
        self._debounced_write.async_shutdown()
        for handle in (self._packet_handle, self._retry_handle, self._watchdog_handle):
            if handle is not None:
                handle.cancel()
        self._packet_handle = self._retry_handle = self._watchdog_handle = None
        tasks = [
            task
            for task in (self._retry_task, self._watchdog_task)
            if task is not None and task is not asyncio.current_task()
        ]
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        self._pending_state = None
        self._pending_updates.clear()
        self._packets.clear()
        # Close the session before waiting for a retry/watchdog task to finish.
        # The session owns and cancels the active BLE operation.
        closing = self._session.start_close()
        self._shutdown_task = asyncio.create_task(
            self._finish_shutdown(closing, tasks), name=f"{DOMAIN} cleanup"
        )
        return self._shutdown_task

    async def _finish_shutdown(self, closing, tasks):
        await super().async_shutdown()
        await asyncio.shield(closing)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def async_shutdown(self):
        task = self.start_shutdown()
        done, _ = await asyncio.wait({task}, timeout=SHUTDOWN_TIMEOUT)
        if not done:
            _LOGGER.warning("Boneco cleanup is still pending; device remains reserved")
            return
        task.result()
