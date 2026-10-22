"""Adversarial BLE timelines, for connection recovery and command handling."""

import asyncio

import pytest
from pyboneco.constants import CHARACTERISTIC_DEVICE_INFO, CHARACTERISTIC_DEVICE_STATE
from test_coordinator import coordinator_for
from test_sessions import INFO, STATE, FakeBleak, transport


def flush(co):
    if co._packet_handle:
        co._packet_handle.cancel()
    co._flush_packets()


def test_notification_between_snapshot_reads_is_not_lost(
    integration, monkeypatch, entry_factory
):
    async def scenario():
        co, clients, _ = coordinator_for(integration, monkeypatch, entry_factory)
        await co.async_refresh()
        original = clients[0].read_gatt_char
        newer = bytearray(INFO)
        newer[4:6] = (1945).to_bytes(2, "little")

        async def read(char):
            if char == CHARACTERISTIC_DEVICE_STATE:
                clients[0].emit(CHARACTERISTIC_DEVICE_INFO, newer)
            return await original(char)

        clients[0].read_gatt_char = read
        await co.async_refresh()
        flush(co)
        actual = co.data.info.particle_raw
        await co.async_shutdown()
        assert actual == 1945

    asyncio.run(scenario())


def test_notification_during_write_readback_is_not_lost(
    integration, monkeypatch, entry_factory
):
    async def scenario():
        co, clients, _ = coordinator_for(integration, monkeypatch, entry_factory)
        await co.async_refresh()
        original = clients[0].read_gatt_char
        newer = bytearray(STATE)
        newer[19] = 9

        async def read(char):
            if char == CHARACTERISTIC_DEVICE_STATE:
                clients[0].emit(char, newer)
                return STATE  # read started before the change notification
            return await original(char)

        clients[0].read_gatt_char = read
        await co.update_state(lambda s: setattr(s, "max_led_brightness", 9))
        await co._async_set_state()
        flush(co)
        actual = co.data.state.max_led_brightness
        await co.async_shutdown()
        assert actual == 9

    asyncio.run(scenario())


def test_brightness_command_preserves_new_physical_lock_state(
    integration, monkeypatch, entry_factory
):
    async def scenario():
        co, clients, _ = coordinator_for(integration, monkeypatch, entry_factory)
        await co.async_refresh()
        await co.update_state(lambda s: setattr(s, "max_led_brightness", 9))
        newer = bytearray(STATE)
        newer[1] |= 4  # user locks the device locally while the write is queued
        clients[0].state = bytes(newer)
        clients[0].emit(CHARACTERISTIC_DEVICE_STATE, newer)
        flush(co)
        await co._async_set_state()
        actual = co.data.state
        await co.async_shutdown()
        assert actual.is_locked
        assert actual.max_led_brightness == 9

    asyncio.run(scenario())


def test_new_command_during_counter_reset_does_not_repeat_the_reset(
    integration, monkeypatch, entry_factory
):
    async def scenario():
        co, clients, _ = coordinator_for(integration, monkeypatch, entry_factory)
        await co.async_refresh()
        entered, release = asyncio.Event(), asyncio.Event()
        original = clients[0].write_gatt_char
        writes = []

        async def write(char, payload, **kwargs):
            if char == CHARACTERISTIC_DEVICE_STATE:
                writes.append(bytes(payload))
                if len(writes) == 1:
                    entered.set()
                    await release.wait()
                await original(char, payload, **kwargs)
                # Device replies with counters, never with reset command markers.
                clients[0].state = (
                    clients[0].state[:4] + bytes(12) + clients[0].state[16:]
                )
            else:
                await original(char, payload, **kwargs)

        clients[0].write_gatt_char = write
        await co.update_state(lambda s: s.set_reminder_filter_date(None))
        task = asyncio.create_task(co._async_set_state())
        await entered.wait()
        await co.update_state(lambda s: setattr(s, "max_led_brightness", 8))
        release.set()
        await task
        await co._async_set_state()
        await co.async_shutdown()
        assert writes[0][4:8] == b"\xff" * 4
        assert writes[1][4:16] == bytes(12)
        assert writes[1][19] == 8

    asyncio.run(scenario())


def test_command_during_write_is_rescheduled_after_debouncer_unlocks(
    integration, monkeypatch, entry_factory
):
    async def scenario():
        co, clients, _ = coordinator_for(integration, monkeypatch, entry_factory)
        await co.async_refresh()
        entered, release, done = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original = clients[0].write_gatt_char
        writes = []

        class LockedDebouncer:
            running = False
            closed = False

            def async_schedule_call(self):
                # HA versions before queued debouncing ignore calls during a job.
                if not self.running and not self.closed:
                    self.running = True
                    asyncio.create_task(self.run())

            async def run(self):
                try:
                    await co._async_set_state()
                finally:
                    self.running = False

            def async_shutdown(self):
                self.closed = True

        async def write(char, payload, **kwargs):
            if char == CHARACTERISTIC_DEVICE_STATE:
                writes.append(bytes(payload))
                if len(writes) == 1:
                    entered.set()
                    await release.wait()
            await original(char, payload, **kwargs)
            if len(writes) == 2:
                done.set()

        co._debounced_write = LockedDebouncer()
        clients[0].write_gatt_char = write
        await co.update_state(lambda s: setattr(s, "max_led_brightness", 10))
        await asyncio.wait_for(entered.wait(), 1)
        await co.update_state(lambda s: setattr(s, "max_led_brightness", 11))
        release.set()
        await asyncio.wait_for(done.wait(), 1)
        await co.async_shutdown()
        assert [payload[19] for payload in writes] == [10, 11]
        assert not co._pending_updates

    asyncio.run(scenario())


def test_shutdown_during_write_does_not_publish_or_leave_an_operation(
    integration, monkeypatch, entry_factory
):
    async def scenario():
        co, clients, _ = coordinator_for(integration, monkeypatch, entry_factory)
        await co.async_refresh()
        entered, release = asyncio.Event(), asyncio.Event()
        original = clients[0].write_gatt_char

        async def write(char, payload, **kwargs):
            entered.set()
            await release.wait()
            return await original(char, payload, **kwargs)

        clients[0].write_gatt_char = write
        await co.update_state(lambda s: setattr(s, "max_led_brightness", 8))
        task = asyncio.create_task(co._async_set_state())
        await entered.wait()
        before = co.updates
        stopping = asyncio.create_task(co.async_shutdown())
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(task, stopping, return_exceptions=True)
        assert co.updates == before
        assert not co._session.connected
        assert co._retry_handle is co._watchdog_handle is None

    asyncio.run(scenario())


def test_partial_subscription_failure_drops_client_and_can_recover(
    integration, monkeypatch
):
    async def scenario():
        session, clients, packets, _, _ = transport(integration, monkeypatch)
        original = FakeBleak.start_notify

        async def subscribe(client, char, callback):
            if len(clients) == 1 and char == CHARACTERISTIC_DEVICE_STATE:
                raise RuntimeError("CCCD rejected")
            await original(client, char, callback)

        monkeypatch.setattr(FakeBleak, "start_notify", subscribe)
        with pytest.raises(RuntimeError, match="CCCD"):
            await session.read()
        assert clients[0].disconnections == 1
        await session.read()
        clients[0].emit(CHARACTERISTIC_DEVICE_INFO, INFO)
        assert packets == []
        assert session.connections == 1  # only fully initialized sessions
        assert CHARACTERISTIC_DEVICE_STATE in clients[1].handlers
        await session.close()

    asyncio.run(scenario())


def test_disconnected_callback_forces_new_client_even_with_stale_connected_flag(
    integration, monkeypatch
):
    async def scenario():
        session, clients, _, _, _ = transport(integration, monkeypatch)
        await session.read()
        clients[0].callback(clients[0])
        await session.read()
        count = len(clients)
        await session.close()
        assert count == 2

    asyncio.run(scenario())


def test_duplicate_packets_still_publish_the_diagnostic_counter(
    integration, monkeypatch, entry_factory
):
    async def scenario():
        co, clients, _ = coordinator_for(integration, monkeypatch, entry_factory)
        await co.async_refresh()
        clients[0].emit(CHARACTERISTIC_DEVICE_INFO, INFO)
        flush(co)
        before = co.updates
        clients[0].emit(CHARACTERISTIC_DEVICE_INFO, INFO)
        flush(co)
        updated = co.updates
        await co.async_shutdown()
        assert updated == before + 1
        assert co.notifications == 2

    asyncio.run(scenario())
