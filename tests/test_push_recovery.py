import asyncio
import logging
from types import SimpleNamespace

import pytest
from pyboneco.constants import (
    CHARACTERISTIC_AUTH,
    CHARACTERISTIC_DEVICE_INFO,
    CHARACTERISTIC_DEVICE_STATE,
)
from test_coordinator import coordinator_for
from test_push_races import flush
from test_sessions import INFO, FakeBleak, transport


def test_close_cancels_stalled_operation_without_waiting_for_read_timeout(
    integration, monkeypatch
):
    async def scenario():
        session, clients, _, _, _ = transport(integration, monkeypatch)
        await session.read()
        clients[0].block = asyncio.Event()
        running = asyncio.create_task(session.read())
        await asyncio.sleep(0)
        await asyncio.wait_for(session.close(), 0.5)
        assert running.cancelled()
        assert not session.connected
        assert not session.busy
        assert clients[0].disconnections == 1

    asyncio.run(scenario())


def test_gatt_operations_are_serialized(integration, monkeypatch):
    async def scenario():
        session, clients, _, _, _ = transport(integration, monkeypatch)
        _, _, state = await session.read()
        original = clients[0].read_gatt_char
        entered, release = asyncio.Event(), asyncio.Event()

        async def read(char):
            entered.set()
            await release.wait()
            return await original(char)

        clients[0].read_gatt_char = read
        task = asyncio.create_task(session.read())
        await entered.wait()
        state.max_led_brightness = 17
        writing = asyncio.create_task(session.write(state))
        await asyncio.sleep(0)
        assert clients[0].state[19] == 20
        release.set()
        await asyncio.gather(task, writing)
        assert clients[0].state[19] == 17
        await session.close()

    asyncio.run(scenario())


def test_repeated_recovery_failures_keep_retrying_then_resubscribe(
    integration, monkeypatch, entry_factory
):
    async def scenario():
        co, clients, mod = coordinator_for(integration, monkeypatch, entry_factory)
        await co.async_refresh()
        clients[0].is_connected = False
        clients[0].callback(clients[0])
        original = mod.PersistentSession.__module__
        session_mod = integration["boneco"].session
        connect = session_mod.establish_connection
        attempts = 0

        async def fail_then_connect(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts < 4:
                raise RuntimeError("adapter temporarily unavailable")
            return await connect(*args, **kwargs)

        monkeypatch.setattr(session_mod, "establish_connection", fail_then_connect)
        for expected_delay in (4, 8, 16, 32):
            co._retry_handle.cancel()
            co._retry_handle = None
            await co._recover()
            if attempts < 4:
                assert not co.last_update_success
                assert co._retry_handle is not None
                assert co._retry_delay == min(60, expected_delay * 2)
        assert co.last_update_success
        assert co.connections == 2
        assert co._retry_delay == 2
        assert original.endswith(".session")
        clients[-1].emit(CHARACTERISTIC_DEVICE_INFO, INFO)
        flush(co)
        assert co.notifications == 1
        await co.async_shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure_after_send", [False, True])
def test_service_reset_is_not_replayed_after_ambiguous_delivery(
    integration, monkeypatch, entry_factory, failure_after_send
):
    async def scenario():
        co, clients, _ = coordinator_for(integration, monkeypatch, entry_factory)
        await co.async_refresh()
        original = clients[0].write_gatt_char

        async def fail(char, payload, **kwargs):
            if failure_after_send:
                await original(char, payload, **kwargs)
                clients[0].error = RuntimeError("readback lost")
            else:
                raise RuntimeError("write reply lost")

        clients[0].write_gatt_char = fail
        await co.update_state(
            lambda s: s.set_reminder_filter_date(None), retry_safe=False
        )
        await co._async_set_state()
        assert not co.last_update_success
        assert not co._pending_updates
        assert co._pending_state is None
        await co.async_refresh()
        await co._async_set_state()
        assert clients[-1].state[4:16] == bytes(12)
        await co.async_shutdown()

    asyncio.run(scenario())


def test_normal_command_is_retried_after_write_failure(
    integration, monkeypatch, entry_factory
):
    async def scenario():
        co, clients, _ = coordinator_for(integration, monkeypatch, entry_factory)
        await co.async_refresh()
        clients[0].error = RuntimeError("GATT failed")
        await co.update_state(lambda s: setattr(s, "max_led_brightness", 7))
        await co._async_set_state()
        assert not co.last_update_success
        assert co._pending_state is not None
        await co.async_refresh()
        await co._async_set_state()
        assert co.data.state.max_led_brightness == 7
        assert co._pending_state is None
        await co.async_shutdown()

    asyncio.run(scenario())


def test_malformed_info_does_not_replace_last_valid_data(
    integration, monkeypatch, entry_factory
):
    async def scenario():
        co, clients, _ = coordinator_for(integration, monkeypatch, entry_factory)
        await co.async_refresh()
        info = co.data.info
        broken = bytearray(INFO)
        broken[0] = 254  # unknown product type
        clients[0].emit(CHARACTERISTIC_DEVICE_INFO, broken)
        flush(co)
        assert co.data.info is info
        assert co.last_update_success
        await co.async_shutdown()

    asyncio.run(scenario())


def test_watchdog_recovers_a_silent_broken_connection(
    integration, monkeypatch, entry_factory
):
    async def scenario():
        co, clients, _ = coordinator_for(integration, monkeypatch, entry_factory)
        await co.async_refresh()
        clients[0].error = RuntimeError("no disconnect callback, GATT dead")
        await co._watchdog()
        assert not co.last_update_success
        assert co._retry_handle is not None
        co._retry_handle.cancel()
        co._retry_handle = None
        await co._recover()
        assert co.last_update_success
        assert co.connections == 2
        assert CHARACTERISTIC_DEVICE_STATE in clients[1].handlers
        await co.async_shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize("notify_active", [False, True])
def test_quiet_notifications_are_renewed_without_reconnecting(
    integration, monkeypatch, notify_active
):
    async def scenario():
        session, clients, _, _, mod = transport(integration, monkeypatch)
        await session.read()
        old = clients[0].handlers[CHARACTERISTIC_DEVICE_INFO]
        session._notification_activity = dict.fromkeys(
            ("info", "state"),
            asyncio.get_running_loop().time() - mod.NOTIFICATION_SILENCE_TIMEOUT - 1,
        )
        if notify_active:
            clients[0].emit(CHARACTERISTIC_DEVICE_INFO, INFO)
            clients[0].emit(CHARACTERISTIC_DEVICE_STATE, clients[0].state)
        await session.read()
        same_handler = clients[0].handlers[CHARACTERISTIC_DEVICE_INFO] is old
        assert same_handler is notify_active
        assert session.connections == 1
        assert clients[0].auth_writes == 1
        await session.close()

    asyncio.run(scenario())


def test_failure_to_renew_notifications_drops_the_connection(integration, monkeypatch):
    async def scenario():
        session, clients, _, _, mod = transport(integration, monkeypatch)
        await session.read()
        session._notification_activity = dict.fromkeys(
            ("info", "state"),
            asyncio.get_running_loop().time() - mod.NOTIFICATION_SILENCE_TIMEOUT - 1,
        )

        async def fail(char):
            raise RuntimeError("cannot re-arm CCCD")

        clients[0].stop_notify = fail
        with pytest.raises(RuntimeError, match="CCCD"):
            await session.read()
        assert not session.connected
        await session.read()
        assert session.connections == 2
        await session.close()

    asyncio.run(scenario())


def test_h700_error_flags_use_protocol_bit_layout(integration):
    cls = integration["boneco"].coordinator.BonecoDeviceInfo
    names = (
        "fds_config_error",
        "fds_log_error",
        "fds_state_error",
        "no_water",
        "no_filter",
        "front_cover_error",
        "fan_error",
        "hum_pack_error",
    )
    for flags in range(256):
        packet = bytearray(INFO)
        packet[1] = flags
        info = cls(packet)
        assert [getattr(info, name) for name in names] == [
            bool(flags & (1 << bit)) for bit in range(8)
        ]


def test_info_missing_values_and_byte_order_use_protocol_layout(integration):
    cls = integration["boneco"].coordinator.BonecoDeviceInfo
    packet = bytearray(INFO)
    packet[2:8] = b"\xff" * 6
    info = cls(packet)
    assert info.temperature is info.humidity is info.particle_raw is info.voc is None
    packet[2:8] = bytes.fromhex("153201020304")
    info = cls(packet)
    assert (info.temperature, info.humidity, info.particle_raw, info.voc) == (
        21,
        50,
        513,
        1027,
    )


def test_common_commands_preserve_unrelated_packet_fields(integration):
    from pyboneco import BonecoModeStatus, BonecoOperationMode

    cls = integration["boneco"].coordinator.BonecoDeviceState
    raw = bytes.fromhex("310a46d57856341234120000efcdab0065070164")
    base = bytearray(raw)
    base[4:16] = bytes(12)  # no-op service counter values
    cases = [
        ("is_enabled", False, 1, 0x02),
        ("is_locked", True, 1, 0x0E),
        ("is_always_history_active", False, 1, 0x08),
        ("target_humidity", 45, 2, 45),
        ("mode_status", BonecoModeStatus.AUTO, 0, 0x35),
        ("operating_mode", BonecoOperationMode(3), 0, 0x33),
    ]
    for attribute, value, offset, expected in cases:
        state = cls("H700", raw)
        setattr(state, attribute, value)
        target = base.copy()
        target[offset] = expected
        assert state.hex_value == bytes(target), attribute


@pytest.mark.parametrize("advertised_name", [None, "Renamed H700"])
def test_h700_protocol_is_selected_from_device_info_not_bluetooth_name(
    integration, monkeypatch, advertised_name
):
    async def scenario():
        session, clients, _, _, _ = transport(integration, monkeypatch)
        session._device_provider = lambda: SimpleNamespace(
            name=advertised_name, address="AA:BB:CC:DD:EE:FF"
        )
        _, _, state = await session.read()
        state.max_led_brightness = 5
        await session.write(state)
        correct_type = state.has_service_operating_counter and not state.is_air_fan
        await session.close()
        assert correct_type
        assert clients[0].state[4:16] == bytes(12)

    asyncio.run(scenario())


def test_authorization_rejection_closes_session(integration, monkeypatch):
    async def scenario():
        session, clients, _, _, _ = transport(integration, monkeypatch)
        original = FakeBleak.write_gatt_char

        async def write(client, char, payload, **kwargs):
            if char == CHARACTERISTIC_AUTH:
                client.handlers[char](SimpleNamespace(uuid=char), bytearray([4, 0, 0]))
            else:
                await original(client, char, payload, **kwargs)

        monkeypatch.setattr(FakeBleak, "write_gatt_char", write)
        with pytest.raises(RuntimeError, match="authorization rejected"):
            await session.read()
        assert not session.connected
        assert clients[0].disconnections == 1
        assert session.connections == 0
        await session.close()

    asyncio.run(scenario())


def test_auth_logs_do_not_contain_device_key(integration, caplog):
    cls = integration["boneco"].session.BonecoAuth
    auth = cls(SimpleNamespace(name="H700", address="AA:BB:CC:DD:EE:FF"))
    key = bytes(range(41, 57))
    with caplog.at_level(logging.DEBUG):
        auth.auth_handler(
            SimpleNamespace(uuid=CHARACTERISTIC_AUTH),
            bytearray(b"\x06\x00\x00" + key + b"\x00"),
        )
    assert auth.save()["key"] == key.hex()
    assert key.hex() not in caplog.text
    assert repr(bytearray(b"\x06\x00\x00" + key + b"\x00")) not in caplog.text


@pytest.mark.parametrize("length", [3, 5, 18])
def test_truncated_pairing_key_does_not_replace_saved_key(integration, length):
    cls = integration["boneco"].session.BonecoAuth
    auth = cls(SimpleNamespace(name="H700", address="AA:BB:CC:DD:EE:FF"), "ab" * 16)
    auth.auth_handler(
        SimpleNamespace(uuid=CHARACTERISTIC_AUTH),
        bytearray(b"\x06\x00\x00" + bytes(length - 3)),
    )
    assert auth.save()["key"] == "ab" * 16


@pytest.mark.parametrize(
    ("speed", "expected"), [(-1, 0), (0, 0), (1, 1), (6, 6), (7, 6)]
)
def test_fan_speed_clamps_to_supported_range(integration, speed, expected):
    state_cls = integration["boneco"].coordinator.BonecoDeviceState
    state = state_cls("H700", bytes.fromhex("3d0a460000000000000000000000000000000164"))
    state.fan_level = speed
    assert (state.hex_value[0] >> 4) & 7 == expected
    assert state.hex_value[0] & 12 == 0  # custom/manual mode
