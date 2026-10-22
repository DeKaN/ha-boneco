import asyncio
from types import SimpleNamespace

import pytest
from pyboneco.constants import (
    CHARACTERISTIC_AUTH,
    CHARACTERISTIC_DEVICE_INFO,
    CHARACTERISTIC_DEVICE_NAME,
    CHARACTERISTIC_DEVICE_STATE,
)

INFO = bytes([13, 0, 21, 50, 77, 0]) + bytes(14)
STATE = bytes([3, 0, 70, 0]) + bytes(12) + bytes([0, 0, 1, 20])


class FakeBleak:
    def __init__(self, disconnected_callback):
        self.is_connected = True
        self.callback = disconnected_callback
        self.handlers = {}
        self.auth_writes = 0
        self.disconnections = 0
        self.error = None
        self.block = None
        self.state = STATE

    async def start_notify(self, char, callback):
        self.handlers[char] = callback
        if char == CHARACTERISTIC_AUTH:
            callback(SimpleNamespace(uuid=char), bytearray([1] + [2] * 19))

    async def stop_notify(self, char):
        self.handlers.pop(char, None)

    async def write_gatt_char(self, char, payload, **kwargs):
        if char == CHARACTERISTIC_AUTH:
            self.auth_writes += 1
            self.handlers[char](SimpleNamespace(uuid=char), bytearray([4, 1, 2]))
        else:
            self.state = bytes(payload)

    async def read_gatt_char(self, char):
        if self.block is not None:
            await self.block.wait()
        if self.error is not None:
            raise self.error
        return {
            CHARACTERISTIC_DEVICE_NAME: b"Renamed H700\0",
            CHARACTERISTIC_DEVICE_INFO: INFO,
            CHARACTERISTIC_DEVICE_STATE: self.state,
        }[char]

    async def disconnect(self):
        self.disconnections += 1
        self.is_connected = False
        self.callback(self)

    def emit(self, char, data):
        self.handlers[char](SimpleNamespace(uuid=char), bytearray(data))


def transport(integration, monkeypatch):
    mod = integration["boneco"].session
    clients = []
    packets = []
    drops = []

    async def connect(_cls, _device, _name, disconnected_callback, **kwargs):
        client = FakeBleak(disconnected_callback)
        clients.append(client)
        return client

    monkeypatch.setattr(mod, "establish_connection", connect)
    device = SimpleNamespace(name="H700", address="AA:BB:CC:DD:EE:FF")
    session = mod.PersistentSession(
        lambda: device,
        "01" * 16,
        on_packet=lambda kind, payload: packets.append((kind, payload)),
        on_disconnect=lambda: drops.append(True),
    )
    return session, clients, packets, drops, mod


def test_reads_and_commands_reuse_connection_and_authorization(
    integration, monkeypatch
):
    async def scenario():
        session, clients, _, drops, _ = transport(integration, monkeypatch)
        _, info, state = await session.read()
        assert info.particle_raw == 77
        await session.read()
        state.max_led_brightness = 10
        readback = await session.write(state)
        assert readback.max_led_brightness == 10
        assert len(clients) == session.connections == 1
        assert clients[0].auth_writes == 1
        assert clients[0].disconnections == 0
        await session.close()
        assert clients[0].disconnections == 1
        assert not drops

    asyncio.run(scenario())


def test_real_disconnect_reconnects_and_restores_subscriptions(
    integration, monkeypatch
):
    async def scenario():
        session, clients, packets, drops, _ = transport(integration, monkeypatch)
        await session.read()
        first = clients[0]
        first.is_connected = False
        first.callback(first)
        assert drops == [True]
        await session.read()
        assert session.connections == 2
        first.emit(CHARACTERISTIC_DEVICE_INFO, INFO)
        assert not packets  # delayed callback from the old connection
        clients[1].emit(CHARACTERISTIC_DEVICE_INFO, INFO)
        assert packets == [("info", INFO)]
        await session.close()

    asyncio.run(scenario())


def test_push_subscribes_to_both_streams_and_ignores_short_packets(
    integration, monkeypatch
):
    async def scenario():
        session, clients, packets, _, _ = transport(integration, monkeypatch)
        await session.read()
        clients[0].emit(CHARACTERISTIC_DEVICE_INFO, b"bad")
        clients[0].emit(CHARACTERISTIC_DEVICE_STATE, STATE)
        assert packets == [("state", STATE)]
        assert session.notifications == 1
        await session.close()
        clients[0].emit(CHARACTERISTIC_DEVICE_INFO, INFO)
        assert session.notifications == 1

    asyncio.run(scenario())


def test_read_error_discards_client_and_next_read_uses_fresh_client(
    integration, monkeypatch
):
    async def scenario():
        session, clients, _, _, _ = transport(integration, monkeypatch)
        await session.read()
        clients[0].error = RuntimeError("GATT failure")
        with pytest.raises(RuntimeError, match="GATT failure"):
            await session.read()
        assert not session.connected
        await session.read()
        assert len(clients) == 2
        await session.close()

    asyncio.run(scenario())


def test_operation_timeout_and_close_finish_and_do_not_reconnect(
    integration, monkeypatch
):
    async def scenario():
        session, clients, _, _, mod = transport(integration, monkeypatch)
        await session.read()
        monkeypatch.setattr(mod, "UPDATE_TIMEOUT", 0.01)
        clients[0].block = asyncio.Event()
        with pytest.raises(TimeoutError):
            await session.read()
        await session.close()
        with pytest.raises(RuntimeError, match="closed"):
            await session.read()
        assert len(clients) == 1

    asyncio.run(scenario())


def test_cancelled_operation_disconnects_and_releases_lock(integration, monkeypatch):
    async def scenario():
        session, clients, _, _, _ = transport(integration, monkeypatch)
        await session.read()
        clients[0].block = asyncio.Event()
        task = asyncio.create_task(session.read())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not session.busy
        assert clients[0].disconnections == 1
        await session.close()

    asyncio.run(scenario())


def test_two_devices_have_independent_authentication_events(integration, monkeypatch):
    async def scenario():
        first, clients1, _, _, _ = transport(integration, monkeypatch)
        first_connect = integration["boneco"].session.establish_connection
        second, clients2, _, _, _ = transport(integration, monkeypatch)
        second_connect = integration["boneco"].session.establish_connection
        second._device_provider = lambda: SimpleNamespace(
            name="H700", address="AA:BB:CC:DD:EE:02"
        )

        async def connect(cls, device, name, **kwargs):
            connector = (
                second_connect if device.address.endswith(":02") else first_connect
            )
            return await connector(cls, device, name, **kwargs)

        monkeypatch.setattr(
            integration["boneco"].session, "establish_connection", connect
        )
        await asyncio.gather(first.read(), second.read())
        assert (
            first._client._auth_data.state_changed
            is not second._client._auth_data.state_changed
        )
        assert clients1[0].auth_writes == clients2[0].auth_writes == 1
        await asyncio.gather(first.close(), second.close())

    asyncio.run(scenario())
