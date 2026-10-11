import asyncio
from types import SimpleNamespace

from pyboneco.constants import CHARACTERISTIC_DEVICE_INFO, CHARACTERISTIC_DEVICE_STATE
from test_sessions import INFO, STATE, transport


def coordinator_for(integration, monkeypatch, entry_factory):
    session, clients, _, _, _ = transport(integration, monkeypatch)
    mod = integration["boneco"].coordinator
    auth = SimpleNamespace(
        address="AA:BB:CC:DD:EE:FF", name="H700", save=lambda: {"key": "01" * 16}
    )
    hass = SimpleNamespace(
        loop=asyncio.get_running_loop(),
        device=SimpleNamespace(address=auth.address, name="H700"),
    )
    coordinator = mod.BonecoDataUpdateCoordinator(hass, entry_factory(), auth, 3)
    coordinator._session = session
    session._on_packet = coordinator._queue_packet
    session._on_disconnect = coordinator._disconnected
    return coordinator, clients, mod


def test_notification_updates_raw_sensor_and_repeated_packets_are_deduplicated(
    integration, monkeypatch, entry_factory
):
    async def scenario():
        co, clients, _ = coordinator_for(integration, monkeypatch, entry_factory)
        await co.async_refresh()
        packet = bytearray(INFO)
        packet[4:6] = (1201).to_bytes(2, "little")
        clients[0].emit(CHARACTERISTIC_DEVICE_INFO, packet)
        co._packet_handle.cancel()
        co._flush_packets()
        assert co.data.info.particle_raw == 1201
        assert co.data.info.particle_value == 3
        info = co.data.info
        updates = co.updates
        clients[0].emit(CHARACTERISTIC_DEVICE_INFO, packet)
        co._packet_handle.cancel()
        co._flush_packets()
        assert co.updates == updates + 1  # diagnostic notification count
        assert co.data.info is info  # payload decoding is still deduplicated
        await co.async_shutdown()

    asyncio.run(scenario())


def test_push_notifications_do_not_postpone_independent_watchdog(
    integration, monkeypatch, entry_factory
):
    async def scenario():
        co, clients, mod = coordinator_for(integration, monkeypatch, entry_factory)
        monkeypatch.setattr(mod, "WATCHDOG_INTERVAL", 0.025)
        await co.async_refresh()
        old_handle = co._watchdog_handle
        clients[0].emit(CHARACTERISTIC_DEVICE_INFO, INFO)
        co._packet_handle.cancel()
        co._flush_packets()
        assert co._watchdog_handle is old_handle
        await asyncio.sleep(0.04)
        assert co._watchdog_handle is not old_handle
        assert len(clients) == 1
        await co.async_shutdown()

    asyncio.run(scenario())


def test_notification_cannot_revert_a_pending_user_command(
    integration, monkeypatch, entry_factory
):
    async def scenario():
        co, clients, _ = coordinator_for(integration, monkeypatch, entry_factory)
        await co.async_refresh()
        await co.update_state(lambda state: setattr(state, "max_led_brightness", 9))
        clients[0].emit(CHARACTERISTIC_DEVICE_STATE, STATE)
        co._packet_handle.cancel()
        co._flush_packets()
        assert co.data.state.max_led_brightness == 9
        await co._async_set_state()
        assert co._pending_state is None
        assert co.data.state.max_led_brightness == 9
        assert len(clients) == 1
        await co.async_shutdown()

    asyncio.run(scenario())


def test_disconnect_marks_unavailable_and_shutdown_cancels_retries(
    integration, monkeypatch, entry_factory
):
    async def scenario():
        co, clients, _ = coordinator_for(integration, monkeypatch, entry_factory)
        await co.async_refresh()
        clients[0].is_connected = False
        clients[0].callback(clients[0])
        assert not co.last_update_success
        assert co._retry_handle is not None
        await co.async_shutdown()
        assert co._retry_handle is co._watchdog_handle is co._packet_handle is None
        co._disconnected()
        assert co._retry_handle is None
        assert len(clients) == 1

    asyncio.run(scenario())


def test_automatic_recovery_creates_a_new_connection(
    integration, monkeypatch, entry_factory
):
    async def scenario():
        co, clients, _ = coordinator_for(integration, monkeypatch, entry_factory)
        await co.async_refresh()
        co._retry_delay = 0.005
        clients[0].is_connected = False
        clients[0].callback(clients[0])
        await asyncio.sleep(0.02)
        assert co.last_update_success
        assert len(clients) == 2
        assert co._retry_handle is None
        await co.async_shutdown()

    asyncio.run(scenario())
