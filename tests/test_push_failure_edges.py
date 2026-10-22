"""Recovery edges at the boundaries between HA, the retry connector and BLE."""

import asyncio

import bleak_retry_connector as connector
import pytest
from bleak.exc import BleakError
from pyboneco.constants import (
    CHARACTERISTIC_AUTH,
    CHARACTERISTIC_DEVICE_INFO,
    CHARACTERISTIC_DEVICE_NAME,
    CHARACTERISTIC_DEVICE_STATE,
)
from test_sessions import INFO, STATE, FakeBleak, transport


def actual_connector(integration, monkeypatch, *, wait_forever=False):
    session, _, packets, drops, mod = transport(integration, monkeypatch)
    clients = []

    class RetryingBleak(FakeBleak):
        def __init__(self, device, disconnected_callback, **kwargs):
            super().__init__(disconnected_callback)
            self.is_connected = False
            self.attempts = 0
            self.entered = asyncio.Event()
            clients.append(self)

        async def connect(self, **kwargs):
            self.attempts += 1
            self.entered.set()
            if wait_forever:
                await asyncio.Event().wait()
            if self.attempts == 1:
                self.callback(self)
                raise BleakError("temporary connection failure")
            self.is_connected = True

    async def no_wait(*args, **kwargs):
        pass

    monkeypatch.setattr(mod, "BleakClient", RetryingBleak)
    monkeypatch.setattr(mod, "establish_connection", connector.establish_connection)
    monkeypatch.setattr(connector, "IS_LINUX", False)
    monkeypatch.setattr(connector, "wait_for_disconnect", no_wait)
    return session, clients, packets, drops


def test_real_connector_can_fail_once_then_connect_successfully(
    integration, monkeypatch
):
    async def scenario():
        session, clients, packets, drops = actual_connector(integration, monkeypatch)
        try:
            await session.read()
            assert clients[0].attempts == 2
            assert session.connections == 1
            assert not drops
            clients[0].emit(CHARACTERISTIC_DEVICE_INFO, INFO)
            assert packets == [("info", INFO)]
        finally:
            await session.close()

    asyncio.run(scenario())


def test_cancel_during_connector_owns_and_disconnects_in_progress_client(
    integration, monkeypatch
):
    async def scenario():
        session, clients, _, _ = actual_connector(
            integration, monkeypatch, wait_forever=True
        )
        task = asyncio.create_task(session.read())
        await asyncio.sleep(0)
        assert clients and clients[0].entered.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        try:
            assert clients[0].disconnections == 1
            assert not session.busy
        finally:
            await session.close()

    asyncio.run(scenario())


def test_active_info_stream_does_not_hide_dead_state_subscription(
    integration, monkeypatch
):
    async def scenario():
        session, clients, _, _, mod = transport(integration, monkeypatch)
        try:
            await session.read()
            old = clients[0].handlers[CHARACTERISTIC_DEVICE_STATE]
            # Simulate silence in State while valid Info keeps arriving.
            session._notification_activity["state"] = (
                asyncio.get_running_loop().time() - mod.NOTIFICATION_SILENCE_TIMEOUT - 1
            )
            clients[0].emit(CHARACTERISTIC_DEVICE_INFO, INFO)
            await session.read()
            assert clients[0].handlers[CHARACTERISTIC_DEVICE_STATE] is not old
            assert session.connections == 1
        finally:
            await session.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["error", "timeout", "cancel"])
@pytest.mark.parametrize(
    ("method", "characteristic", "writing"),
    [
        ("start_notify", CHARACTERISTIC_AUTH, False),
        ("write_gatt_char", CHARACTERISTIC_AUTH, False),
        ("start_notify", CHARACTERISTIC_DEVICE_INFO, False),
        ("start_notify", CHARACTERISTIC_DEVICE_STATE, False),
        ("read_gatt_char", CHARACTERISTIC_DEVICE_INFO, False),
        ("read_gatt_char", CHARACTERISTIC_DEVICE_NAME, False),
        ("read_gatt_char", CHARACTERISTIC_DEVICE_STATE, False),
        ("write_gatt_char", CHARACTERISTIC_DEVICE_STATE, True),
        ("read_gatt_char", CHARACTERISTIC_DEVICE_STATE, True),
    ],
)
def test_each_failed_gatt_stage_allows_a_fresh_working_session(
    integration, monkeypatch, method, characteristic, writing, failure
):
    """Inject a failure at each transport stage, then use the restored streams."""

    async def scenario():
        session, clients, packets, _, mod = transport(integration, monkeypatch)
        if writing:
            _, _, state = await session.read()
        original = getattr(FakeBleak, method)
        entered = asyncio.Event()

        async def inject(client, char, *args, **kwargs):
            if client is clients[0] and char == characteristic:
                entered.set()
                if failure == "error":
                    raise BleakError("injected link loss")
                await asyncio.Event().wait()
            return await original(client, char, *args, **kwargs)

        monkeypatch.setattr(FakeBleak, method, inject)
        monkeypatch.setattr(mod, "UPDATE_TIMEOUT", 0.03)
        operation = session.write(state) if writing else session.read()
        task = asyncio.create_task(operation)
        try:
            await asyncio.wait_for(entered.wait(), 0.5)
            if failure == "cancel":
                task.cancel()
            expected = {
                "error": BleakError,
                "timeout": TimeoutError,
                "cancel": asyncio.CancelledError,
            }[failure]
            with pytest.raises(expected):
                await task
            assert not session.busy
            assert not session.connected
            assert clients[0].disconnections == 1

            await session.read()
            assert len(clients) == 2
            clients[-1].emit(CHARACTERISTIC_DEVICE_INFO, INFO)
            clients[-1].emit(CHARACTERISTIC_DEVICE_STATE, STATE)
            assert packets[-2:] == [("info", INFO), ("state", STATE)]
            assert session.connected
        finally:
            await session.close()

    asyncio.run(scenario())


def test_disconnect_timeout_does_not_hold_the_session_lock(integration, monkeypatch):
    async def scenario():
        session, clients, _, _, mod = transport(integration, monkeypatch)
        await session.read()

        async def stalled_disconnect():
            await asyncio.Event().wait()

        clients[0].disconnect = stalled_disconnect
        clients[0].error = BleakError("connection broken")
        monkeypatch.setattr(mod, "DISCONNECT_TIMEOUT", 0.02)
        try:
            with pytest.raises(BleakError, match="connection broken"):
                await asyncio.wait_for(session.read(), 0.5)
            assert not session.busy
            await session.read()
            assert session.connected
            assert session.connections == 2
        finally:
            await session.close()

    asyncio.run(scenario())


def test_malformed_stream_does_not_count_as_healthy_notifications(
    integration, monkeypatch
):
    async def scenario():
        session, clients, packets, _, _ = transport(integration, monkeypatch)
        try:
            await session.read()
            broken = bytearray(INFO)
            broken[0] = 254
            clients[0].emit(CHARACTERISTIC_DEVICE_INFO, broken)
            assert session.notifications == 0
            assert packets == []
            clients[0].emit(CHARACTERISTIC_DEVICE_STATE, STATE)
            assert session.notifications == 1
        finally:
            await session.close()

    asyncio.run(scenario())
