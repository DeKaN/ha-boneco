"""Keep polling as the default and release its connection after every operation."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
import voluptuous as vol
from bleak.exc import BleakError
from pyboneco.constants import (
    CHARACTERISTIC_AUTH,
    CHARACTERISTIC_DEVICE_INFO,
    CHARACTERISTIC_DEVICE_NAME,
    CHARACTERISTIC_DEVICE_STATE,
)
from test_coordinator import coordinator_for
from test_sessions import FakeBleak, transport
from test_push_shutdown import ignore_cancellation_until
from test_push_shutdown import lifecycle as lifecycle


@pytest.mark.parametrize(
    "options", [{}, {"connection_mode": "polling"}, {"connection_mode": "persistent"}]
)
def test_options_show_default_or_saved_mode(integration, entry_factory, options):
    entry = entry_factory()
    entry.options = options
    flow = integration["boneco"].flow.BonecoConfigFlow.async_get_options_flow(entry)
    flow.config_entry = entry
    form = asyncio.run(flow.async_step_init())
    schema = form["data_schema"]
    assert schema({}) == {"connection_mode": options.get("connection_mode", "polling")}
    for mode in ("polling", "persistent"):
        assert schema({"connection_mode": mode}) == {"connection_mode": mode}
    with pytest.raises(vol.Invalid):
        schema({"connection_mode": "unknown"})


@pytest.mark.parametrize("mode", ["polling", "persistent"])
def test_options_change_only_mode_and_preserve_pairing(
    integration, entry_factory, mode
):
    entry = entry_factory()
    pairing = entry.data.copy()
    entry.options = {"unrelated_option": 123}
    flow = integration["boneco"].flow.BonecoConfigFlow.async_get_options_flow(entry)
    flow.config_entry = entry
    result = asyncio.run(flow.async_step_init({"connection_mode": mode}))
    assert result["data"] == {"unrelated_option": 123, "connection_mode": mode}
    assert entry.data == pairing


def test_polling_reads_and_writes_disconnect_and_do_not_subscribe(
    integration, monkeypatch
):
    async def scenario():
        session, clients, packets, drops, _ = transport(integration, monkeypatch)
        session._persistent = False
        _, _, state = await session.read()
        await session.read()
        state.max_led_brightness = 8
        readback = await session.write(state)
        assert readback.max_led_brightness == 8
        assert len(clients) == session.connections == 3
        assert all(
            client.auth_writes == client.disconnections == 1 for client in clients
        )
        assert all(
            not client.is_connected and not client.handlers for client in clients
        )
        assert not session.connected
        assert not packets and not drops
        await session.close()
        assert all(client.disconnections == 1 for client in clients)

    asyncio.run(scenario())


def test_default_coordinator_polls_without_reconnect_or_watchdog_timers(
    integration, monkeypatch, entry_factory
):
    async def scenario():
        co, clients, _ = coordinator_for(
            integration, monkeypatch, entry_factory, persistent=False
        )
        assert not co.config_entry.options
        assert co.update_interval == timedelta(seconds=60)
        await co.async_refresh()
        assert co.last_update_success
        assert co._watchdog_handle is co._retry_handle is None
        assert not co._session.connected
        await co.update_state(lambda state: setattr(state, "max_led_brightness", 8))
        await co._async_set_state()
        assert co.data.state.max_led_brightness == 8
        assert co.last_update_success
        assert len(clients) == 2
        assert all(not client.is_connected for client in clients)
        assert co.notifications == 0
        assert co._watchdog_handle is co._retry_handle is None
        await co.async_shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["error", "timeout", "cancel"])
@pytest.mark.parametrize(
    "method, characteristic, writing",
    [
        ("start_notify", CHARACTERISTIC_AUTH, False),
        ("read_gatt_char", CHARACTERISTIC_DEVICE_INFO, False),
        ("read_gatt_char", CHARACTERISTIC_DEVICE_NAME, False),
        ("read_gatt_char", CHARACTERISTIC_DEVICE_STATE, False),
        ("write_gatt_char", CHARACTERISTIC_DEVICE_STATE, True),
        ("read_gatt_char", CHARACTERISTIC_DEVICE_STATE, True),
    ],
)
def test_polling_failure_closes_transport_and_next_operation_recovers(
    integration, monkeypatch, method, characteristic, writing, failure
):
    async def scenario():
        session, clients, _, drops, mod = transport(integration, monkeypatch)
        session._persistent = False
        state = None
        if writing:
            _, _, state = await session.read()
        failed_index = len(clients)
        entered = asyncio.Event()
        original = getattr(FakeBleak, method)

        async def inject(client, char, *args, **kwargs):
            if client is clients[failed_index] and char == characteristic:
                entered.set()
                if failure == "error":
                    raise BleakError("injected polling failure")
                await asyncio.Event().wait()
            return await original(client, char, *args, **kwargs)

        monkeypatch.setattr(FakeBleak, method, inject)
        monkeypatch.setattr(mod, "UPDATE_TIMEOUT", 0.03)
        task = asyncio.create_task(session.write(state) if writing else session.read())
        await asyncio.wait_for(entered.wait(), 0.5)
        if failure == "cancel":
            task.cancel()
        with pytest.raises(
            {
                "error": BleakError,
                "timeout": TimeoutError,
                "cancel": asyncio.CancelledError,
            }[failure]
        ):
            await task
        assert not session.connected and not session.busy
        assert clients[failed_index].disconnections == 1
        await session.read()
        assert all(not client.is_connected for client in clients)
        assert all(client.disconnections == 1 for client in clients)
        assert not drops
        await session.close()

    asyncio.run(scenario())


def test_devices_can_use_different_connection_modes(
    integration, monkeypatch, entry_factory
):
    async def scenario():
        polling, polled_clients, _ = coordinator_for(
            integration, monkeypatch, entry_factory, persistent=False
        )
        # Initialize polling before replacing the fake connector for the other device.
        await polling.async_refresh()
        push, push_clients, _ = coordinator_for(integration, monkeypatch, entry_factory)
        await push.async_refresh()
        assert polling.last_update_success and push.last_update_success
        assert polled_clients[0].disconnections == 1
        assert push_clients[0].is_connected
        assert polling._watchdog_handle is None
        assert push._watchdog_handle is not None
        assert push.update_interval is None
        await polling.async_shutdown()
        await push.async_shutdown()

    asyncio.run(scenario())


def test_mode_update_reloads_only_its_entry_without_changing_pairing(
    lifecycle, entry_factory
):
    entry = entry_factory("chosen_device")
    pairing = entry.data.copy()
    reloads = []

    async def reload(entry_id):
        reloads.append(entry_id)

    hass = SimpleNamespace(config_entries=SimpleNamespace(async_reload=reload))
    entry.options = {"connection_mode": "persistent"}
    asyncio.run(lifecycle._async_update_listener(hass, entry))
    assert reloads == [entry.entry_id]
    assert entry.data == pairing


def test_polling_shutdown_retains_cleanup_when_backend_delays_cancellation(
    integration, monkeypatch
):
    async def scenario():
        session, clients, _, drops, mod = transport(integration, monkeypatch)
        session._persistent = False
        entered, release = asyncio.Event(), asyncio.Event()
        original = FakeBleak.read_gatt_char

        async def read(client, char):
            await ignore_cancellation_until(release, entered)
            return await original(client, char)

        monkeypatch.setattr(FakeBleak, "read_gatt_char", read)
        monkeypatch.setattr(mod, "CLOSE_TIMEOUT", 0.02)
        task = asyncio.create_task(session.read())
        await entered.wait()
        try:
            with pytest.raises(TimeoutError, match="cleanup"):
                await asyncio.wait_for(session.close(), 0.5)
            assert not session._close_task.done()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await session.close()
        assert not session.busy
        assert clients[0].disconnections == 1
        assert not drops

    asyncio.run(scenario())
