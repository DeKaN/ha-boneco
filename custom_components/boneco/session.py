"""Serialized persistent GATT session with bounded recovery and stale callbacks."""

import asyncio
import logging
from collections.abc import Callable

from bleak import BleakClient
from bleak_retry_connector import establish_connection
from pyboneco import BonecoDeviceInfo, BonecoDeviceState
from pyboneco.auth import BonecoAuth
from pyboneco.client import BonecoClient
from pyboneco.constants import (
    CHARACTERISTIC_DEVICE_INFO,
    CHARACTERISTIC_DEVICE_STATE,
    SUPPORTED_DEVICE_CLASSES_BY_MODEL,
)

from .const import DISCONNECT_TIMEOUT, UPDATE_TIMEOUT

_LOGGER = logging.getLogger(__name__)
NOTIFICATION_SILENCE_TIMEOUT = 600
CLOSE_TIMEOUT = 10


class PersistentSession:
    def __init__(
        self,
        device_provider: Callable,
        key: str,
        *,
        on_packet: Callable,
        on_disconnect: Callable,
    ):
        self._device_provider = device_provider
        self._key = key
        self._on_packet = on_packet
        self._on_disconnect = on_disconnect
        self._lock = asyncio.Lock()
        self._generation = 0
        self._client = None
        self._closed = False
        self._connection_lost = False
        self._active_operation = None
        self._operation_done = None
        self._close_task = None
        self._latest_packets = {}
        self._notification_activity = {}
        self.model_name = None
        self.connections = 0
        self.notifications = 0

    @property
    def connected(self):
        return (
            not self._connection_lost
            and self._client is not None
            and self._client.is_connected
        )

    @property
    def busy(self):
        return self._lock.locked()

    def _disconnected(self, generation, _client):
        if generation == self._generation and not self._closed:
            self._connection_lost = True
            self._generation += 1
            self._latest_packets.clear()
            self._on_disconnect()

    def _packet(self, generation, kind, _sender, payload):
        if generation != self._generation or self._closed:
            return
        if len(payload) < 20:
            _LOGGER.warning("Ignoring truncated Boneco %s notification", kind)
            return
        try:
            if kind == "info":
                info = BonecoDeviceInfo(payload)
                if info.device_type != self.model_name:
                    raise ValueError("notification identifies a different model")
            else:
                BonecoDeviceState(self.model_name, payload)
        except (IndexError, KeyError, ValueError) as err:
            _LOGGER.warning("Ignoring malformed Boneco %s notification: %s", kind, err)
            return
        self.notifications += 1
        self._notification_activity[kind] = asyncio.get_running_loop().time()
        self._latest_packets[kind] = (self.notifications, bytes(payload))
        self._on_packet(kind, bytes(payload))

    async def _connect_locked(self):
        if self._closed:
            raise RuntimeError("Boneco session has been closed")
        if self.connected:
            return
        await self._drop_locked()
        self._generation += 1
        generation = self._generation
        self._connection_lost = False
        device = self._device_provider()
        if device is None:
            raise RuntimeError("Boneco device is not currently discoverable")
        auth = BonecoAuth(device, self._key)
        client = self._client = BonecoClient(auth, persistent_session=True)
        connection_ready = False

        def disconnected(transport):
            # The retry connector may disconnect between its internal attempts.
            # Arm session recovery only once it has returned a connected client.
            if connection_ready:
                self._disconnected(generation, transport)

        class OwnedBleakClient(BleakClient):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                # Retain the real client even if establish_connection is cancelled.
                client._client = self

        client._client = await establish_connection(
            OwnedBleakClient,
            device,
            device.name or device.address,
            disconnected_callback=disconnected,
            max_attempts=3,
        )
        connection_ready = True
        if not self.connected:
            raise RuntimeError("Boneco disconnected before authorization")
        await client.authorize(timeout=UPDATE_TIMEOUT)
        # BlueZ may expose a friendly name or no advertising name after reconnect.
        # DeviceInfo identifies the actual protocol/model independently of it.
        info = await client.get_device_info()
        if info.device_type not in SUPPORTED_DEVICE_CLASSES_BY_MODEL:
            raise RuntimeError("Unsupported Boneco model in DeviceInfo")
        self.model_name = auth.model_name = info.device_type
        await self._subscribe(client, generation)
        if generation != self._generation or not self.connected:
            raise RuntimeError("Boneco disconnected during session setup")
        self.connections += 1

    async def _subscribe(self, client, generation, *, restart=False):
        for kind, characteristic in (
            ("info", CHARACTERISTIC_DEVICE_INFO),
            ("state", CHARACTERISTIC_DEVICE_STATE),
        ):
            if restart:
                await client._client.stop_notify(characteristic)
            await client._client.start_notify(
                characteristic,
                lambda sender, payload, kind=kind: self._packet(
                    generation, kind, sender, payload
                ),
            )
            self._notification_activity[kind] = asyncio.get_running_loop().time()

    async def _drop_locked(self):
        self._generation += 1
        self._latest_packets.clear()
        client, self._client = self._client, None
        if client is None:
            return
        try:
            async with asyncio.timeout(DISCONNECT_TIMEOUT):
                await client.disconnect()
        except Exception as err:  # noqa: BLE001 - preserve the operation error
            _LOGGER.warning("Could not close Boneco session: %s", err)

    async def _run(self, operation):
        # A lock-wait timeout must not tear down another caller's active operation.
        async with asyncio.timeout(UPDATE_TIMEOUT + DISCONNECT_TIMEOUT + 1):
            async with self._lock:
                self._active_operation = asyncio.current_task()
                self._operation_done = asyncio.get_running_loop().create_future()
                try:
                    async with asyncio.timeout(UPDATE_TIMEOUT):
                        await self._connect_locked()
                        result = await operation(self._client)
                        if not self.connected or self._closed:
                            raise RuntimeError("Boneco disconnected during operation")
                        return result
                except BaseException:
                    await self._drop_locked()
                    raise
                finally:
                    self._active_operation = None
                    self._operation_done.set_result(None)

    def _newer_packet(self, kind, since, fallback):
        """Keep a notification arriving during/after a GATT read of that field."""
        serial, payload = self._latest_packets.get(kind, (0, None))
        if serial <= since:
            return fallback
        try:
            if kind == "info":
                return BonecoDeviceInfo(payload)
            return BonecoDeviceState(self._client._auth_data.name, payload)
        except (IndexError, KeyError, ValueError) as err:
            _LOGGER.warning("Ignoring malformed Boneco %s notification: %s", kind, err)
            return fallback

    async def _read_state(self, client):
        since = self.notifications
        state = await client.get_state()
        return self._newer_packet("state", since, state)

    async def read(self):
        async def operation(client):
            now = asyncio.get_running_loop().time()
            if any(
                now - last_packet >= NOTIFICATION_SILENCE_TIMEOUT
                for last_packet in self._notification_activity.values()
            ):
                _LOGGER.debug("Renewing quiet Boneco notification subscriptions")
                await self._subscribe(client, self._generation, restart=True)
            # Initial/readback snapshots use real GATT reads, not UI state.
            name = await client.get_device_name()
            info_since = self.notifications
            info = await client.get_device_info()
            state = await self._read_state(client)
            info = self._newer_packet("info", info_since, info)
            return name, info, state

        return await self._run(operation)

    async def write(self, state, *, on_write_attempt=None):
        async def operation(client):
            # Merge user intent into a fresh read while holding the GATT lock.
            # An offline/queued brightness command must not restore an old mode.
            outgoing = state
            if callable(state):
                outgoing = state(await self._read_state(client))
            # A backend can delay cancellation while the fresh read is in flight.
            # Do not send the queued command if shutdown started in that time.
            if self._closed or not self.connected:
                raise RuntimeError("Boneco session closed before command write")
            if on_write_attempt is not None:
                on_write_attempt()
            await client.set_state(outgoing)
            return await self._read_state(client)

        return await self._run(operation)

    def start_close(self):
        """Retain cleanup even if a caller stops waiting for a stuck driver."""
        if self._close_task is not None:
            return self._close_task
        self._closed = True
        self._generation += 1
        self._close_task = asyncio.create_task(
            self._finish_close(), name="Boneco session cleanup"
        )
        return self._close_task

    async def _finish_close(self):
        active = self._active_operation
        if active is not None and active is not asyncio.current_task():
            if not active.cancelling():
                active.cancel()
            # Wait for the BLE operation, not its caller's whole HA setup task.
            # That caller can itself await close() in its failure cleanup.
            await asyncio.shield(self._operation_done)
        async with self._lock:
            await self._drop_locked()

    async def close(self):
        task = self.start_close()
        # asyncio.wait does not wait for the driver to acknowledge cancellation.
        done, _ = await asyncio.wait({task}, timeout=CLOSE_TIMEOUT)
        if not done:
            raise TimeoutError("Boneco Bluetooth cleanup is still pending")
        task.result()
