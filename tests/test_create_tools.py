"""Tests for the dedicated create tools (category / variable / event).

Each tool orchestrates a small sequence of IP-Symcon calls (create → set parent →
set name → ...). IP-Symcon is mocked at the ``_client`` seam; these tests pin the
exact call sequence, the JSON result shape and the IPS_ENABLE_WRITE gate.
"""

import asyncio
import json
from unittest.mock import AsyncMock, call, patch

import ipsymcon_mcp.server as server
from ipsymcon_mcp.server import (
    CreateCategoryInput,
    CreateEventInput,
    CreateVariableInput,
    ips_create_category,
    ips_create_event,
    ips_create_variable,
)


def _run(coro):
    return asyncio.run(coro)


def test_create_category(monkeypatch):
    monkeypatch.setenv("IPS_ENABLE_WRITE", "true")
    fake = AsyncMock()
    fake.call.side_effect = [True, 555, None, None]  # ObjectExists, CreateCategory, SetParent, SetName
    with patch.object(server, "_client", return_value=fake):
        out = _run(ips_create_category(CreateCategoryInput(parent_id=0, name="Sensors")))
    assert json.loads(out) == {"category_id": 555, "name": "Sensors", "parent_id": 0, "ok": True}
    assert fake.call.await_args_list == [
        call("IPS_ObjectExists", [0]),
        call("IPS_CreateCategory", []),
        call("IPS_SetParent", [555, 0]),
        call("IPS_SetName", [555, "Sensors"]),
    ]


def test_create_variable_with_profile(monkeypatch):
    monkeypatch.setenv("IPS_ENABLE_WRITE", "true")
    fake = AsyncMock()
    fake.call.side_effect = [True, 777, None, None, None]
    with patch.object(server, "_client", return_value=fake):
        out = _run(ips_create_variable(CreateVariableInput(
            parent_id=12, name="Temp", variable_type="float", profile="~Temperature")))
    assert json.loads(out) == {
        "variable_id": 777, "name": "Temp", "parent_id": 12,
        "type": "float", "profile": "~Temperature", "ok": True,
    }
    assert fake.call.await_args_list == [
        call("IPS_ObjectExists", [12]),
        call("IPS_CreateVariable", [2]),
        call("IPS_SetParent", [777, 12]),
        call("IPS_SetName", [777, "Temp"]),
        call("IPS_SetVariableCustomProfile", [777, "~Temperature"]),
    ]


def test_create_variable_without_profile_skips_profile_call(monkeypatch):
    monkeypatch.setenv("IPS_ENABLE_WRITE", "true")
    fake = AsyncMock()
    fake.call.side_effect = [True, 778, None, None]
    with patch.object(server, "_client", return_value=fake):
        out = _run(ips_create_variable(CreateVariableInput(
            parent_id=0, name="Flag", variable_type="boolean")))
    data = json.loads(out)
    assert data["variable_id"] == 778
    assert data["type"] == "boolean"
    assert data["profile"] is None
    assert fake.call.await_args_list == [
        call("IPS_ObjectExists", [0]),
        call("IPS_CreateVariable", [0]),
        call("IPS_SetParent", [778, 0]),
        call("IPS_SetName", [778, "Flag"]),
    ]


def test_create_event(monkeypatch):
    monkeypatch.setenv("IPS_ENABLE_WRITE", "true")
    fake = AsyncMock()
    fake.call.side_effect = [True, 888, None, None, None]
    with patch.object(server, "_client", return_value=fake):
        out = _run(ips_create_event(CreateEventInput(
            parent_id=5, name="Nightly", event_type="cyclic", active=True)))
    assert json.loads(out) == {
        "event_id": 888, "name": "Nightly", "parent_id": 5,
        "type": "cyclic", "active": True, "ok": True,
    }
    assert fake.call.await_args_list == [
        call("IPS_ObjectExists", [5]),
        call("IPS_CreateEvent", [1]),
        call("IPS_SetParent", [888, 5]),
        call("IPS_SetName", [888, "Nightly"]),
        call("IPS_SetEventActive", [888, True]),
    ]


def test_create_tool_blocked_when_write_disabled(monkeypatch):
    monkeypatch.setenv("IPS_ENABLE_WRITE", "false")
    fake = AsyncMock()
    with patch.object(server, "_client", return_value=fake):
        out = _run(ips_create_category(CreateCategoryInput(parent_id=0, name="X")))
    assert "IPS_ENABLE_WRITE" in out
    fake.call.assert_not_awaited()
