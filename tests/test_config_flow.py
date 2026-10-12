"""Pairing failures must not create invalid entries or leak discovered devices."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace


def test_discoveries_are_not_shared_between_flows(integration):
    cls = integration["boneco"].flow.BonecoConfigFlow
    first, second = cls(), cls()
    first._discovered_advs["AA:BB:CC:DD:EE:01"] = object()
    assert not second._discovered_advs


def test_missing_pairing_key_aborts_instead_of_creating_entry(integration):
    flow = integration["boneco"].flow.BonecoConfigFlow()
    flow._auth_data = SimpleNamespace(save=lambda: {"name": "H700", "key": ""})
    assert asyncio.run(flow.async_step_confirm()) == {
        "type": "abort",
        "reason": "invalid_auth",
    }


def test_unsupported_model_aborts_instead_of_raising_key_error(integration):
    flow = integration["boneco"].flow.BonecoConfigFlow()
    flow._auth_data = SimpleNamespace(
        save=lambda: {"name": "Unknown", "key": "01" * 16}
    )
    assert asyncio.run(flow.async_step_confirm()) == {
        "type": "abort",
        "reason": "not_supported",
    }


def test_new_pairing_abort_reasons_have_user_facing_messages(integration):
    root = Path(integration["boneco"].flow.__file__).parent
    for name in ("strings.json", "translations/en.json", "translations/ru.json"):
        aborts = json.loads((root / name).read_text(encoding="utf-8"))["config"]["abort"]
        assert aborts["invalid_auth"]
        assert aborts["not_supported"]
