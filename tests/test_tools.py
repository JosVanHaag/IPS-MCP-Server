"""Unit tests for the read tools, the write gate and error mapping.

IP-Symcon is the external boundary and is mocked at the ``_client`` seam, so these
tests pin each tool's own behavior (which IPS method it calls, how it shapes the
JSON result, the IPS_ENABLE_WRITE gate, and the actionable error messages).
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import ipsymcon_mcp.server as server
from ipsymcon_mcp.client import IPSConfigError, IPSError
from ipsymcon_mcp.server import (
    FindByNameInput,
    ObjIdInput,
    ScriptIdInput,
    SetValueInput,
    VarIdInput,
    ips_find_object_by_name,
    ips_get_object,
    ips_get_script_content,
    ips_get_value,
    ips_get_variable,
    ips_list_children,
    ips_set_value,
)


def _run(coro):
    return asyncio.run(coro)


def _client_returning(value=None, *, side_effect=None):
    fake = AsyncMock()
    if side_effect is not None:
        fake.call.side_effect = side_effect
    else:
        fake.call.return_value = value
    return fake


# --- Read tools (always available, no gate) ---------------------------------


def test_get_value_returns_value():
    fake = _client_returning(21.5)
    with patch.object(server, "_client", return_value=fake):
        out = _run(ips_get_value(VarIdInput(variable_id=12345)))
    assert json.loads(out) == {"variable_id": 12345, "value": 21.5}
    fake.call.assert_awaited_once_with("GetValue", [12345])


def test_get_variable_maps_metadata():
    meta = {"VariableType": 2, "VariableProfile": "Temperature",
            "VariableAction": 0, "VariableUpdated": 0, "VariableChanged": 0}
    fake = _client_returning(side_effect=[meta, 21.5, "Wohnzimmer Temp", "Raeume\\WZ\\Temp"])
    with patch.object(server, "_client", return_value=fake):
        out = _run(ips_get_variable(VarIdInput(variable_id=12345)))
    data = json.loads(out)
    assert data["name"] == "Wohnzimmer Temp"
    assert data["type"] == "Float"
    assert data["profile"] == "Temperature"
    assert data["value"] == 21.5
    assert data["has_action"] is False
    assert data["action_script_id"] == 0
    assert data["path"] == "Raeume\\WZ\\Temp"


def test_get_object_enriches_type_name():
    fake = _client_returning({"ObjectType": 2, "ObjectName": "Temp", "ParentID": 0})
    with patch.object(server, "_client", return_value=fake):
        out = _run(ips_get_object(ObjIdInput(object_id=12345)))
    assert json.loads(out)["ObjectTypeName"] == "Variable"


def test_list_children_shape():
    fake = _client_returning(side_effect=[
        [10, 20],
        {"ObjectName": "Cat A", "ObjectType": 0},
        {"ObjectName": "Var B", "ObjectType": 2},
    ])
    with patch.object(server, "_client", return_value=fake):
        out = _run(ips_list_children(ObjIdInput(object_id=0)))
    data = json.loads(out)
    assert data["count"] == 2
    assert data["children"][0] == {"id": 10, "name": "Cat A", "type": "Category"}
    assert data["children"][1] == {"id": 20, "name": "Var B", "type": "Variable"}


def test_find_object_by_name():
    fake = _client_returning(23942)
    with patch.object(server, "_client", return_value=fake):
        out = _run(ips_find_object_by_name(FindByNameInput(name="MCP Test", parent_id=0)))
    assert json.loads(out)["object_id"] == 23942
    fake.call.assert_awaited_once_with("IPS_GetObjectIDByName", ["MCP Test", 0])


def test_get_script_content():
    fake = _client_returning("<?php echo 1;")
    with patch.object(server, "_client", return_value=fake):
        out = _run(ips_get_script_content(ScriptIdInput(script_id=5)))
    assert json.loads(out) == {"script_id": 5, "content": "<?php echo 1;"}


# --- Write gate -------------------------------------------------------------


def test_set_value_blocked_when_write_disabled(monkeypatch):
    monkeypatch.setenv("IPS_ENABLE_WRITE", "false")
    fake = AsyncMock()
    with patch.object(server, "_client", return_value=fake):
        out = _run(ips_set_value(SetValueInput(variable_id=1, value=True)))
    assert "IPS_ENABLE_WRITE" in out
    fake.call.assert_not_awaited()


def test_set_value_works_when_write_enabled(monkeypatch):
    monkeypatch.setenv("IPS_ENABLE_WRITE", "true")
    fake = _client_returning(True)
    with patch.object(server, "_client", return_value=fake):
        out = _run(ips_set_value(SetValueInput(variable_id=99, value=42)))
    assert json.loads(out) == {"variable_id": 99, "value": 42, "ok": True}
    fake.call.assert_awaited_once_with("SetValue", [99, 42])


# --- Error mapping ----------------------------------------------------------


def test_config_error_maps_to_actionable_message():
    fake = AsyncMock()
    fake.call.side_effect = IPSConfigError("IPS_URL is not set")
    with patch.object(server, "_client", return_value=fake):
        out = _run(ips_get_value(VarIdInput(variable_id=1)))
    assert "Configuration problem" in out
    assert "IPS_URL" in out


def test_ips_error_maps_to_actionable_message():
    fake = AsyncMock()
    fake.call.side_effect = IPSError(-32603, "Invalid object")
    with patch.object(server, "_client", return_value=fake):
        out = _run(ips_get_object(ObjIdInput(object_id=999)))
    assert "IP-Symcon returned error" in out
    assert "Invalid object" in out
