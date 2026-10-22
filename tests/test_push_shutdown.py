"""Exercise unload while the backend delays or ignores cancellation."""

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from pyboneco import BonecoDeviceClass
from test_coordinator import coordinator_for
from test_guard import hass_for
from test_sessions import transport


async def ignore_cancellation_until(release, entered):
    entered.set()
    while not release.is_set():
        try:
            await release.wait()
        except asyncio.CancelledError:
            pass


async def stalled_read(client):
    entered, release = asyncio.Event(), asyncio.Event()
    original = client.read_gatt_char

    async def read(char):
        await ignore_cancellation_until(release, entered)
        return await original(char)

    client.read_gatt_char = read
    return entered, release


@pytest.fixture
def lifecycle(integration, monkeypatch):
    core = ModuleType("homeassistant.core")
    core.HomeAssistant = object
    monkeypatch.setitem(sys.modules, core.__name__, core)
    monkeypatch.setattr(
        sys.modules["homeassistant.const"],
        "EVENT_HOMEASSISTANT_STOP",
        "stop",
        raising=False,
    )
    path = Path(integration["boneco"].session.__file__).with_name("__init__.py")
    spec = importlib.util.spec_from_file_location(
        "test_boneco.lifecycle", path, submodule_search_locations=None
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "test_boneco"
    spec.loader.exec_module(mod)
    return mod


def attach_entry(co, entry_factory):
    entry = co.config_entry
    entry.data["sensor_type"] = BonecoDeviceClass.TOP_CLIMATE.value
    entry.runtime_data = co
    other = entry_factory("other", "boneco")
    base = hass_for([entry, other])
    co.hass.data = base.data
    co.hass.config_entries = base.config_entries
    return entry, other


@pytest.mark.parametrize("stage", ["read", "disconnect"])
def test_close_has_a_deadline_even_if_backend_ignores_cancellation(
    integration, monkeypatch, stage
):
    async def scenario():
        session, clients, _, _, mod = transport(integration, monkeypatch)
        await session.read()
        entered, release = asyncio.Event(), asyncio.Event()
        operation = None
        if stage == "read":
            entered, release = await stalled_read(clients[0])
            operation = asyncio.create_task(session.read())
            await entered.wait()
        else:
            original = clients[0].disconnect

            async def disconnect():
                await ignore_cancellation_until(release, entered)
                await original()

            clients[0].disconnect = disconnect
        monkeypatch.setattr(mod, "CLOSE_TIMEOUT", 0.02)
        try:
            with pytest.raises(TimeoutError, match="cleanup"):
                await asyncio.wait_for(session.close(), 0.5)
            assert not session._close_task.done()
        finally:
            release.set()
            if operation:
                await asyncio.gather(operation, return_exceptions=True)
            await session.close()
        assert not session.busy
        assert not session.connected
        assert clients[0].disconnections == 1

    asyncio.run(scenario())


def test_concurrent_shutdown_callers_both_wait_for_cleanup(
    integration, monkeypatch, entry_factory
):
    async def scenario():
        co, clients, _ = coordinator_for(integration, monkeypatch, entry_factory)
        await co.async_refresh()
        entered, release = await stalled_read(clients[0])
        running = asyncio.create_task(co.async_refresh())
        await entered.wait()
        first = asyncio.create_task(co.async_shutdown())
        await asyncio.sleep(0)
        second = asyncio.create_task(co.async_shutdown())
        await asyncio.sleep(0)
        try:
            assert not first.done()
            assert not second.done()
        finally:
            release.set()
            await asyncio.gather(first, second, running, return_exceptions=True)
        assert co._shutdown_task.done()
        assert clients[0].disconnections == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_caller", [False, True])
def test_unload_keeps_lease_until_backend_finishes(
    integration, monkeypatch, entry_factory, lifecycle, cancel_caller
):
    async def scenario():
        co, clients, mod = coordinator_for(integration, monkeypatch, entry_factory)
        entry, other = attach_entry(co, entry_factory)
        lifecycle.claim(co.hass, entry)
        await co.async_refresh()
        entered, release = await stalled_read(clients[0])
        running = asyncio.create_task(co.async_refresh())
        await entered.wait()

        async def unload(*args):
            return True

        co.hass.config_entries.async_unload_platforms = unload
        monkeypatch.setattr(mod, "SHUTDOWN_TIMEOUT", 0.02)
        caller = asyncio.create_task(lifecycle.async_unload_entry(co.hass, entry))
        try:
            if cancel_caller:
                await asyncio.sleep(0)
                caller.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await caller
            else:
                # HA must be able to retry setup, not enter FAILED_UNLOAD.
                assert await asyncio.wait_for(caller, 0.5) is True
            assert not co._shutdown_task.done()
            # Another entry must wait until the transport cleanup finishes.
            with pytest.raises(RuntimeError):
                integration["boneco"].guard.claim(co.hass, other)
            with pytest.raises(RuntimeError, match="cleanup"):
                await lifecycle.async_setup_entry(co.hass, entry)
        finally:
            release.set()
            await asyncio.gather(running, return_exceptions=True)
            await co.async_shutdown()
            await asyncio.sleep(0)
        assert not co.hass.data[lifecycle._CLEANUP_KEY]
        integration["boneco"].guard.claim(co.hass, other)

    asyncio.run(scenario())


def test_failed_setup_also_retains_lease_until_cleanup(
    integration, monkeypatch, entry_factory, lifecycle
):
    async def scenario():
        co, clients, mod = coordinator_for(integration, monkeypatch, entry_factory)
        entry, other = attach_entry(co, entry_factory)
        await co.async_refresh()
        entered, release = await stalled_read(clients[0])
        running = asyncio.create_task(co.async_refresh())
        await entered.wait()
        entry.async_on_unload = lambda fn: None
        co.hass.bus = type(
            "Bus", (), {"async_listen_once": lambda *args: lambda: None}
        )()

        async def failed_refresh():
            raise RuntimeError("setup read failed")

        co.async_config_entry_first_refresh = failed_refresh
        monkeypatch.setattr(lifecycle, "BonecoDataUpdateCoordinator", lambda *args: co)
        monkeypatch.setattr(mod, "SHUTDOWN_TIMEOUT", 0.02)
        try:
            with pytest.raises(RuntimeError, match="setup read failed"):
                await asyncio.wait_for(lifecycle.async_setup_entry(co.hass, entry), 0.5)
            with pytest.raises(RuntimeError):
                integration["boneco"].guard.claim(co.hass, other)
        finally:
            release.set()
            await asyncio.gather(running, return_exceptions=True)
            await co.async_shutdown()
            await asyncio.sleep(0)
        integration["boneco"].guard.claim(co.hass, other)

    asyncio.run(scenario())


def test_no_write_after_shutdown_during_fresh_state_read(
    integration, monkeypatch, entry_factory
):
    async def scenario():
        co, clients, _ = coordinator_for(integration, monkeypatch, entry_factory)
        await co.async_refresh()
        entered, release = await stalled_read(clients[0])
        await co.update_state(lambda s: setattr(s, "max_led_brightness", 7))
        running = asyncio.create_task(co._async_set_state())
        await entered.wait()
        closing = asyncio.create_task(co.async_shutdown())
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(running, closing, return_exceptions=True)
        assert clients[0].state[19] == 20
        assert not co._pending_updates

    asyncio.run(scenario())


def test_unload_only_releases_device_after_platforms_and_transport_close(
    integration, monkeypatch, entry_factory, lifecycle
):
    async def scenario():
        co, clients, _ = coordinator_for(integration, monkeypatch, entry_factory)
        entry, other = attach_entry(co, entry_factory)
        lifecycle.claim(co.hass, entry)
        await co.async_refresh()
        allow_unload = False

        async def unload(*args):
            return allow_unload

        co.hass.config_entries.async_unload_platforms = unload
        try:
            assert await lifecycle.async_unload_entry(co.hass, entry) is False
            assert not co._stopped
            assert co._session.connected
            with pytest.raises(RuntimeError):
                integration["boneco"].guard.claim(co.hass, other)
            allow_unload = True
            assert await lifecycle.async_unload_entry(co.hass, entry) is True
            assert clients[0].disconnections == 1
            integration["boneco"].guard.claim(co.hass, other)
        finally:
            await co.async_shutdown()

    asyncio.run(scenario())


def test_close_does_not_wait_for_callers_own_cleanup(integration, monkeypatch):
    async def scenario():
        session, clients, _, _, mod = transport(integration, monkeypatch)
        await session.read()
        clients[0].block = asyncio.Event()
        monkeypatch.setattr(mod, "CLOSE_TIMEOUT", 0.1)

        async def caller():
            try:
                await session.read()
            finally:
                # Like an HA setup task which cleans up a failed first refresh.
                await session.close()

        running = asyncio.create_task(caller())
        await asyncio.sleep(0)
        await asyncio.wait_for(session.close(), 0.5)
        result = await asyncio.gather(running, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert clients[0].disconnections == 1

    asyncio.run(scenario())
