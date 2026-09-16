#!/usr/bin/env python3
"""MCP server for IP-Symcon.

Lets an LLM/agent inspect and **develop** an IP-Symcon home-automation system over
its JSON-RPC API — read the object tree, read/edit/create PHP scripts, read variables,
and (when explicitly enabled) control devices and modify the running system.

Safety model:
    * Read tools are always available.
    * Write/dev tools and the generic ``ips_call`` gateway require the environment
      variable ``IPS_ENABLE_WRITE`` to be truthy. This is the deliberate guardrail
      against an agent silently modifying a live home-automation system. Recommended:
      enable only against a test/staging instance and keep a backup.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from . import skills
from .client import IPSClient, IPSConfigError, IPSError
from .config import instance_write_enabled, make_client

# Load .env from the project root if present, so credentials are available when the
# server is launched as an MCP subprocess regardless of the working directory.
try:
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

# Keep httpx's per-request INFO logging out of the server's stderr.
import logging  # noqa: E402

logging.getLogger("httpx").setLevel(logging.WARNING)

mcp = FastMCP("ipsymcon_mcp", instructions=skills.INSTRUCTIONS)

# Die Runbooks aus skills/ als MCP-Prompts anbieten. Sie liegen ohnehin im Repo und
# werden als Claude-Code-Plugin ausgeliefert — über die MCP-Schnittstelle erreichen
# sie zusätzlich jeden Client, der das Plugin nicht kennt. Siehe skills.py.
skills.register(mcp)

# --- Constants ---------------------------------------------------------------

OBJECT_TYPES = {0: "Category", 1: "Instance", 2: "Variable", 3: "Script", 4: "Event", 5: "Media", 6: "Link"}
VARIABLE_TYPES = {0: "Boolean", 1: "Integer", 2: "Float", 3: "String"}
VAR_TYPE_TO_INT = {"boolean": 0, "integer": 1, "float": 2, "string": 3}
EVENT_TYPE_TO_INT = {"triggered": 0, "cyclic": 1, "weekly": 2}

IPSValue = bool | int | float | str

# Prefixes of IP-Symcon's own documented API. A third-party module registers its
# functions under its own prefix, and nothing stops such a module from naming a
# writing function "..._GetAndReset". Read-through therefore stays inside the core
# surface, where "Get"/"Exists" reliably means "reads".
CORE_FUNCTION_PREFIXES = frozenset({
    "AC", "ALL", "CC", "CMI", "CSCK", "Cutter", "DMX", "DS", "EIB", "ENO", "FHT", "FHZ",
    "FS20", "HC", "HID", "HM", "HMS", "IG", "IMAP", "IPS", "IRT", "KNX", "LCN", "MBUS",
    "MC", "MQTT", "MSCK", "MXC", "NC", "OC", "OW", "OZW", "PC", "PJ", "POP3", "RegVar",
    "S7", "SC", "SMS", "SMTP", "SPRT", "SSCK", "Sys", "TTS", "UC", "USCK", "UVR",
    "VELLEUSB", "VIO", "VOIP", "WAC", "WC", "WFC", "WWW", "WinLIRC", "WuT", "XBee",
    "ZW", "ModBus",
})

# Reading functions that are nevertheless gated: they do not change the system, but they
# carry integration credentials (router, cloud and hub passwords/keys) into the model
# context and the client logs. The same reason that keeps `include_configuration` off by
# default in ips_export_subtree — read-through must not reopen that door.
# IPS_GetSnapshot is the worst of them: it returns the whole object tree including every
# instance configuration in a single call.
CREDENTIAL_BEARING_METHODS = frozenset({
    "IPS_GetConfiguration",
    "IPS_GetSnapshot",
    "IPS_GetSnapshotChanges",
    "WFC_GetSnapshotChanges",
    "WFC_GetSnapshotChangesEx",
})

READ_GATEWAY_REFUSED_MSG = (
    "Error: ips_call_read only accepts reading functions of the documented core API. "
    "'{method}' is not one of them — use ips_call for it (requires IPS_ENABLE_WRITE)."
)

WRITE_DISABLED_MSG = (
    "Error: Write/dev tools are disabled (safety default). Set IPS_ENABLE_WRITE=true in the "
    "environment/.env to allow modifying the live IP-Symcon system. Recommendation: enable this "
    "only against a test/staging instance and create a backup first."
)

# --- Shared helpers ----------------------------------------------------------


def _is_read_only_method(method: str) -> bool:
    """Can this IP-Symcon function only read?

    IP-Symcon names its API by what a function does: ``Get``/``Exists`` read,
    ``Set``/``Create``/``Delete``/``Run``/``Apply`` change something. Checked against the
    817 functions of the documented API (9.0): 187 match, and the six that carry a
    verb-looking noun — ``IPS_GetAvailableUpdates``, ``IPS_GetKernelRunlevel``,
    ``IPS_GetLiveUpdateVersion``, ``IPS_GetSnapshotChanges``, ``WFC_GetSnapshotChanges``,
    ``WFC_GetSnapshotChangesEx`` — all really only read.

    The naming convention is the whole guarantee, so it is only trusted where IP-Symcon
    controls the name: a core prefix, or one of the unprefixed value getters. Everything
    else stays behind the write gate.

    Second limit, and it is not about writing: the readers in CREDENTIAL_BEARING_METHODS are
    excluded because they pull integration credentials into the model context.
    """
    # Credential-bearing readers stay behind the gate regardless of their name
    if method in CREDENTIAL_BEARING_METHODS:
        return False

    prefix, sep, _ = method.partition("_")
    if sep:
        return prefix in CORE_FUNCTION_PREFIXES and ("Get" in method or "Exists" in method)
    # Unprefixed core functions: GetValue, GetValueBoolean, ... (SetValue/RequestAction do not match)
    return method.startswith("Get")


def _global_write_enabled() -> bool:
    return os.environ.get("IPS_ENABLE_WRITE", "").strip().lower() in {"1", "true", "yes", "on"}


def _write_enabled(instance: str | None = None) -> bool:
    """May this call write to *this* instance?

    Both gates must agree: the process-wide ``IPS_ENABLE_WRITE`` **and** the
    per-instance ``enable_write`` from the instances YAML. An instance that says
    nothing inherits the global flag — that keeps every existing single-instance
    setup working unchanged.

    ⚠️ **The point is the asymmetry.** Adding ``enable_write: true`` to an instance
    can never grant writes on its own; the global flag stays the master switch.
    But ``enable_write: false`` on an instance **can take them away**, even with the
    global flag on. That is what lets you open writes for a migration target while
    the lived-in house stays read-only — the case the README recommended and the
    code did not support (security review 2026-08-15).
    """
    if not _global_write_enabled():
        return False
    per_instance = instance_write_enabled(instance)
    return True if per_instance is None else per_instance


def _client(instance: str | None = None) -> IPSClient:
    """Build a client for the named instance (or the default)."""
    return make_client(instance)


def _ts(value: Any) -> Any:
    """Convert a unix timestamp to an ISO-8601 UTC string, leaving other values untouched."""
    try:
        if value:
            return datetime.fromtimestamp(int(value), tz=UTC).isoformat()
    except (ValueError, TypeError, OSError):
        pass
    return value


def _handle_error(e: Exception) -> str:
    """Map exceptions to clear, actionable error strings for the agent."""
    if isinstance(e, IPSConfigError):
        return f"Error: Configuration problem — {e}. Set IPS_URL (and IPS_USER/IPS_PASSWORD if required)."
    if isinstance(e, IPSError):
        return (
            f"Error: IP-Symcon returned error {e.code}: {e.message}. "
            "Check the object/variable ID and that the called function exists."
        )
    if isinstance(e, httpx.HTTPStatusError):
        sc = e.response.status_code
        if sc in (401, 403):
            return (
                f"Error: Authentication failed (HTTP {sc}). Check IPS_USER/IPS_PASSWORD and that the "
                "JSON-RPC API access is enabled for that user in IP-Symcon."
            )
        return f"Error: IP-Symcon server returned HTTP {sc}."
    if isinstance(e, httpx.ConnectError):
        return (
            "Error: Could not connect to IP-Symcon. Check IPS_URL host/port (default port 3777) "
            "and that the server is reachable on the network."
        )
    if isinstance(e, httpx.TimeoutException):
        return "Error: Request timed out — IP-Symcon did not respond in time."
    return f"Error: Unexpected error: {type(e).__name__}: {e}"


def _dumps(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


# --- Input models ------------------------------------------------------------


class _Base(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")

    instance: str | None = Field(
        default=None,
        description="Named IP-Symcon instance to target (from IPS_INSTANCES_FILE); omit for the default.",
    )


class VarIdInput(_Base):
    variable_id: int = Field(..., description="IP-Symcon variable object ID (e.g. 12345)", ge=1)


class ObjIdInput(_Base):
    object_id: int = Field(..., description="IP-Symcon object ID. Root of the tree is 0.", ge=0)


class FindByNameInput(_Base):
    name: str = Field(..., description="Exact object name to look up", min_length=1)
    parent_id: int = Field(default=0, description="Parent object ID to search under (0 = root)", ge=0)


class ScriptIdInput(_Base):
    script_id: int = Field(..., description="IP-Symcon script object ID", ge=1)


class SetValueInput(_Base):
    variable_id: int = Field(..., description="Target variable object ID", ge=1)
    value: IPSValue = Field(..., description="New value; type must match the variable (bool/int/float/string)")


class RunScriptInput(_Base):
    script_id: int = Field(..., description="Script object ID to execute", ge=1)


class RunScriptCaptureInput(_Base):
    script_id: int = Field(..., description="Script object ID to execute", ge=1)
    parameters: dict[str, str] = Field(
        default_factory=dict,
        description="Optional named parameters passed to the script (available there as $_IPS['key'])",
    )


class SetScriptContentInput(_Base):
    script_id: int = Field(..., description="Script object ID whose PHP content to replace", ge=1)
    content: str = Field(..., description="New full PHP source code (including <?php ... ?> if applicable)")


class CreateScriptInput(_Base):
    parent_id: int = Field(..., description="Parent object ID (category/root) to place the script under", ge=0)
    name: str = Field(..., description="Name for the new script", min_length=1)
    content: str = Field(default="", description="Initial PHP source code")


class CreateCategoryInput(_Base):
    parent_id: int = Field(..., description="Parent object ID (category/root) to place the category under", ge=0)
    name: str = Field(..., description="Name for the new category", min_length=1)


class CreateVariableInput(_Base):
    parent_id: int = Field(..., description="Parent object ID to place the variable under", ge=0)
    name: str = Field(..., description="Name for the new variable", min_length=1)
    variable_type: Literal["boolean", "integer", "float", "string"] = Field(
        ..., description="Variable data type")
    profile: str = Field(default="", description="Optional variable profile to attach, e.g. '~Temperature'")


class CreateEventInput(_Base):
    parent_id: int = Field(..., description="Parent object ID to place the event under", ge=0)
    name: str = Field(..., description="Name for the new event", min_length=1)
    event_type: Literal["triggered", "cyclic", "weekly"] = Field(
        ..., description="Event type: triggered (on a variable change), cyclic (interval), or weekly (schedule)")
    active: bool = Field(default=False, description="Whether the event is active right after creation")


class CallInput(_Base):
    method: str = Field(..., description="IP-Symcon function name, e.g. 'IPS_CreateVariable'", min_length=1)
    params: list[Any] = Field(default_factory=list, description="Positional parameter list for the function")


class GetVariableByPathInput(_Base):
    path: str = Field(..., description="Object path from the base, e.g. 'Räume/Büro/Zustand'", min_length=1)
    base_id: int = Field(default=0, description="Object ID the path is relative to (0 = root)", ge=0)
    separator: str = Field(default="/", description="Path separator", min_length=1)


class SnapshotVariablesInput(_Base):
    variable_ids: list[int] = Field(..., description="Variable object IDs to snapshot", min_length=1)


class DiffVariablesInput(_Base):
    before: dict[str, IPSValue] = Field(
        ..., description="A previous snapshot's 'variables' map (id -> value) to diff against the live values")


class GetObjectTreeInput(_Base):
    root_id: int = Field(default=0, description="Root object ID to start from (0 = tree root)", ge=0)
    max_depth: int = Field(default=3, description="How many levels deep to descend", ge=1, le=20)


class ExportSubtreeInput(_Base):
    root_id: int = Field(..., description="Root object ID of the subtree to export", ge=0)
    max_depth: int = Field(default=25, description="How many levels deep to export", ge=1, le=50)
    include_configuration: bool = Field(
        default=False,
        description=(
            "Include each instance's IPS_GetConfiguration block. OFF by default: those blocks "
            "regularly carry integration credentials (router, cloud and hub passwords/keys). "
            "Only turn this on for an actual backup/restore, and be aware the values then enter "
            "the model context."
        ),
    )


class ImportSubtreeInput(_Base):
    tree: dict[str, Any] = Field(
        ..., description="A subtree JSON as produced by ips_export_subtree (nested {id,name,type,...,children}).")
    target_parent_id: int = Field(
        ..., description="Object ID under which to recreate the subtree root (0 = tree root).", ge=0)


# --- Read tools --------------------------------------------------------------


@mcp.tool(
    name="ips_get_value",
    annotations={"title": "Get variable value", "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True},
)
async def ips_get_value(params: VarIdInput) -> str:
    """Read the current value of an IP-Symcon variable (GetValue).

    Returns a JSON object: {"variable_id": int, "value": bool|int|float|str}.
    Use ips_get_variable for metadata (type, profile, last change).
    """
    try:
        value = await _client(params.instance).call("GetValue", [params.variable_id])
        return _dumps({"variable_id": params.variable_id, "value": value})
    except Exception as e:  # noqa: BLE001 — mapped to actionable message
        return _handle_error(e)


@mcp.tool(
    name="ips_get_variable",
    annotations={"title": "Get variable metadata", "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True},
)
async def ips_get_variable(params: VarIdInput) -> str:
    """Read full metadata + current value of a variable (IPS_GetVariable + GetValue + name).

    Returns JSON: {variable_id, name, type, profile, value, has_action, updated, changed}.
    'updated'/'changed' are ISO timestamps. 'type' is Boolean/Integer/Float/String.
    """
    try:
        client = _client(params.instance)
        meta = await client.call("IPS_GetVariable", [params.variable_id])
        value = await client.call("GetValue", [params.variable_id])
        name = await client.call("IPS_GetName", [params.variable_id])
        out = {
            "variable_id": params.variable_id,
            "name": name,
            "type": VARIABLE_TYPES.get(meta.get("VariableType"), meta.get("VariableType")),
            "profile": meta.get("VariableProfile") or meta.get("VariableCustomProfile") or None,
            "value": value,
            "has_action": bool(meta.get("VariableAction", 0)) or bool(meta.get("VariableCustomAction", 0)),
            "updated": _ts(meta.get("VariableUpdated")),
            "changed": _ts(meta.get("VariableChanged")),
        }
        return _dumps(out)
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


@mcp.tool(
    name="ips_get_object",
    annotations={"title": "Get object metadata", "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True},
)
async def ips_get_object(params: ObjIdInput) -> str:
    """Read metadata of any object in the IP-Symcon tree (IPS_GetObject).

    Returns the raw IPS object dict enriched with 'ObjectTypeName'
    (Category/Instance/Variable/Script/Event/Media/Link). Includes ParentID and ChildrenIDs
    so you can traverse the tree. Root object is ID 0.
    """
    try:
        obj = await _client(params.instance).call("IPS_GetObject", [params.object_id])
        if isinstance(obj, dict):
            obj = {**obj, "ObjectTypeName": OBJECT_TYPES.get(obj.get("ObjectType"), obj.get("ObjectType"))}
        return _dumps(obj)
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


@mcp.tool(
    name="ips_list_children",
    annotations={"title": "List child objects", "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True},
)
async def ips_list_children(params: ObjIdInput) -> str:
    """List the direct children of an object with id, name and type (browse the tree).

    Call with object_id=0 to list the top level. Returns JSON:
    {"parent_id": int, "count": int, "children": [{"id", "name", "type"}]}.
    """
    try:
        client = _client(params.instance)
        child_ids = await client.call("IPS_GetChildrenIDs", [params.object_id])
        children = []
        for cid in child_ids or []:
            obj = await client.call("IPS_GetObject", [cid])
            obj = obj if isinstance(obj, dict) else {}
            children.append({
                "id": cid,
                "name": obj.get("ObjectName"),
                "type": OBJECT_TYPES.get(obj.get("ObjectType"), obj.get("ObjectType")),
            })
        return _dumps({"parent_id": params.object_id, "count": len(children), "children": children})
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


@mcp.tool(
    name="ips_find_object_by_name",
    annotations={"title": "Find object by name", "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True},
)
async def ips_find_object_by_name(params: FindByNameInput) -> str:
    """Resolve an object ID from its exact name under a given parent (IPS_GetObjectIDByName).

    Returns JSON {"name", "parent_id", "object_id"}. Errors if no exact match exists —
    use ips_list_children to browse if you only know part of the name.
    """
    try:
        oid = await _client(params.instance).call("IPS_GetObjectIDByName", [params.name, params.parent_id])
        return _dumps({"name": params.name, "parent_id": params.parent_id, "object_id": oid})
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


@mcp.tool(
    name="ips_get_script_content",
    annotations={"title": "Read script PHP source", "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True},
)
async def ips_get_script_content(params: ScriptIdInput) -> str:
    """Read the PHP source code of a script object (IPS_GetScriptContent).

    Returns JSON {"script_id": int, "content": str}. Use this before editing a script
    with ips_set_script_content so you work from the current source.
    """
    try:
        content = await _client(params.instance).call("IPS_GetScriptContent", [params.script_id])
        return _dumps({"script_id": params.script_id, "content": content})
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


# --- Navigation / observation tools (read-only) ------------------------------


async def _resolve_path(client: IPSClient, path: str, base_id: int, separator: str) -> int:
    """Resolve an object path (names separated by `separator`) to an object ID."""
    current = base_id
    for segment in (s for s in path.split(separator) if s):
        current = await client.call("IPS_GetObjectIDByName", [segment, current])
    return current


@mcp.tool(
    name="ips_get_variable_by_path",
    annotations={"title": "Get variable value by path", "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True},
)
async def ips_get_variable_by_path(params: GetVariableByPathInput) -> str:
    """Read a variable's value by its object path instead of its ID (GetValue).

    Walks the tree via IPS_GetObjectIDByName for each path segment, then reads the value.
    Example path: 'Räume/Büro/Zustand'. Returns JSON {path, variable_id, value}.
    """
    try:
        client = _client(params.instance)
        oid = await _resolve_path(client, params.path, params.base_id, params.separator)
        value = await client.call("GetValue", [oid])
        return _dumps({"path": params.path, "variable_id": oid, "value": value})
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


@mcp.tool(
    name="ips_snapshot_variables",
    annotations={"title": "Snapshot variable values", "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True},
)
async def ips_snapshot_variables(params: SnapshotVariablesInput) -> str:
    """Capture the current values of a set of variables (GetValue per id).

    Returns JSON {"variables": {id: value}, "count": int}. Keep the returned snapshot and
    pass its 'variables' map to ips_diff_variables after a change to see what moved.
    """
    try:
        client = _client(params.instance)
        snapshot = {}
        for vid in params.variable_ids:
            snapshot[str(vid)] = await client.call("GetValue", [vid])
        return _dumps({"variables": snapshot, "count": len(snapshot)})
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


@mcp.tool(
    name="ips_diff_variables",
    annotations={"title": "Diff variables against a snapshot", "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True},
)
async def ips_diff_variables(params: DiffVariablesInput) -> str:
    """Compare a previous snapshot against the live values (GetValue per id).

    'before' is a snapshot's 'variables' map (id -> value). Returns JSON
    {"changed": {id: {before, after}}, "changed_count": int, "unchanged_count": int} —
    the build → run → see-what-changed loop for agentic development.
    """
    try:
        client = _client(params.instance)
        changed = {}
        unchanged = 0
        for id_str, before_val in params.before.items():
            after_val = await client.call("GetValue", [int(id_str)])
            if after_val != before_val:
                changed[id_str] = {"before": before_val, "after": after_val}
            else:
                unchanged += 1
        return _dumps({"changed": changed, "changed_count": len(changed), "unchanged_count": unchanged})
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


async def _build_subtree(client: IPSClient, oid: int, depth: int, max_depth: int) -> dict[str, Any]:
    """Recursively build a {id, name, type, children?} node up to max_depth."""
    obj = await client.call("IPS_GetObject", [oid])
    obj = obj if isinstance(obj, dict) else {}
    node = {"id": oid, "name": obj.get("ObjectName"),
            "type": OBJECT_TYPES.get(obj.get("ObjectType"), obj.get("ObjectType"))}
    if depth < max_depth:
        child_ids = await client.call("IPS_GetChildrenIDs", [oid])
        children = [await _build_subtree(client, cid, depth + 1, max_depth) for cid in (child_ids or [])]
        if children:
            node["children"] = children
    return node


@mcp.tool(
    name="ips_get_object_tree",
    annotations={"title": "Get a nested object subtree", "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True},
)
async def ips_get_object_tree(params: GetObjectTreeInput) -> str:
    """Fetch a whole subtree at once as nested {id, name, type, children} (up to max_depth).

    Far fewer round-trips than walking ips_list_children level by level. Mind the depth on
    large installations — each node costs an IPS_GetObject + IPS_GetChildrenIDs call.
    Returns the nested tree JSON rooted at root_id.
    """
    try:
        tree = await _build_subtree(_client(params.instance), params.root_id, 0, params.max_depth)
        return _dumps(tree)
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


async def _export_node(
    client: IPSClient, oid: int, depth: int, max_depth: int, include_configuration: bool = False
) -> dict[str, Any]:
    """Recursively serialize a node with the type-specific detail needed to recreate it."""
    obj = await client.call("IPS_GetObject", [oid])
    obj = obj if isinstance(obj, dict) else {}
    otype = obj.get("ObjectType")
    node: dict[str, Any] = {"id": oid, "name": obj.get("ObjectName"), "type": OBJECT_TYPES.get(otype, otype)}
    if otype == 2:  # Variable
        meta = await client.call("IPS_GetVariable", [oid])
        meta = meta if isinstance(meta, dict) else {}
        node["variable_type"] = VARIABLE_TYPES.get(meta.get("VariableType"), meta.get("VariableType"))
        node["profile"] = meta.get("VariableProfile") or meta.get("VariableCustomProfile") or None
        node["value"] = await client.call("GetValue", [oid])
    elif otype == 3:  # Script
        node["content"] = await client.call("IPS_GetScriptContent", [oid])
    elif otype == 4:  # Event
        node["event"] = await client.call("IPS_GetEvent", [oid])
    elif otype == 1:  # Instance
        inst = await client.call("IPS_GetInstance", [oid])
        inst = inst if isinstance(inst, dict) else {}
        node["module_id"] = (inst.get("ModuleInfo") or {}).get("ModuleID")
        # ⚠️ Instance configurations regularly hold integration credentials (FritzBox,
        # Hue, VeSync, cloud keys). Handing them out from a tool marked readOnlyHint
        # turns "just reading" into credential exfiltration into the model context and
        # the client logs. Off unless explicitly asked for (security review 2026-08-15).
        if include_configuration:
            node["configuration"] = await client.call("IPS_GetConfiguration", [oid])
        else:
            node["configuration_omitted"] = "set include_configuration=true to export it"
    elif otype == 6:  # Link
        link = await client.call("IPS_GetLink", [oid])
        link = link if isinstance(link, dict) else {}
        node["target_id"] = link.get("TargetID")
    if depth < max_depth:
        child_ids = await client.call("IPS_GetChildrenIDs", [oid])
        children = [
            await _export_node(client, cid, depth + 1, max_depth, include_configuration)
            for cid in (child_ids or [])
        ]
        if children:
            node["children"] = children
    return node


@mcp.tool(
    name="ips_export_subtree",
    annotations={"title": "Export a subtree for backup/restore", "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True},
)
async def ips_export_subtree(params: ExportSubtreeInput) -> str:
    """Serialize a whole subtree to rich JSON for backup or migration (read-only).

    Like ips_get_object_tree but carries the detail needed to recreate each node:
    variables (type, profile, value), scripts (content), events (IPS_GetEvent), instances
    (module_id + configuration) and links (target_id). The restore/adaptation side
    (recreating objects + remapping IDs to a target system) is an agentic workflow, not part
    of this read-only export. Returns the nested JSON rooted at root_id.
    """
    try:
        tree = await _export_node(
            _client(params.instance), params.root_id, 0, params.max_depth,
            params.include_configuration,
        )
        return _dumps(tree)
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


# --- Write / dev tools (gated by IPS_ENABLE_WRITE) ---------------------------


@mcp.tool(
    name="ips_set_value",
    annotations={"title": "Set variable value", "readOnlyHint": False, "destructiveHint": True,
                 "idempotentHint": True, "openWorldHint": True},
)
async def ips_set_value(params: SetValueInput) -> str:
    """Set a variable's value directly (SetValue). Requires IPS_ENABLE_WRITE.

    Note: SetValue writes the variable without triggering its action. To actuate a device
    that has an action attached, prefer ips_request_action. Returns JSON {variable_id, value, ok}.
    """
    if not _write_enabled(params.instance):
        return WRITE_DISABLED_MSG
    try:
        await _client(params.instance).call("SetValue", [params.variable_id, params.value])
        return _dumps({"variable_id": params.variable_id, "value": params.value, "ok": True})
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


@mcp.tool(
    name="ips_request_action",
    annotations={"title": "Trigger variable action", "readOnlyHint": False, "destructiveHint": True,
                 "idempotentHint": True, "openWorldHint": True},
)
async def ips_request_action(params: SetValueInput) -> str:
    """Actuate a device by requesting an action on its variable (RequestAction). Requires IPS_ENABLE_WRITE.

    This is the correct way to control actuators (lights, switches): it runs the variable's
    action handler instead of only writing the value. Returns JSON {variable_id, value, ok}.
    """
    if not _write_enabled(params.instance):
        return WRITE_DISABLED_MSG
    try:
        await _client(params.instance).call("RequestAction", [params.variable_id, params.value])
        return _dumps({"variable_id": params.variable_id, "value": params.value, "ok": True})
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


@mcp.tool(
    name="ips_run_script",
    annotations={"title": "Run a script", "readOnlyHint": False, "destructiveHint": True,
                 "idempotentHint": False, "openWorldHint": True},
)
async def ips_run_script(params: RunScriptInput) -> str:
    """Execute an IP-Symcon script (IPS_RunScript). Requires IPS_ENABLE_WRITE.

    Side effects depend entirely on the script. Returns JSON {script_id, ok}.
    """
    if not _write_enabled(params.instance):
        return WRITE_DISABLED_MSG
    try:
        await _client(params.instance).call("IPS_RunScript", [params.script_id])
        return _dumps({"script_id": params.script_id, "ok": True})
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


@mcp.tool(
    name="ips_run_script_capture",
    annotations={"title": "Run a script and capture its return value", "readOnlyHint": False,
                 "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
)
async def ips_run_script_capture(params: RunScriptCaptureInput) -> str:
    """Execute a script and capture its output (IPS_RunScriptWaitEx). Requires IPS_ENABLE_WRITE.

    Unlike ips_run_script (fire-and-confirm), this returns what the script produces — the
    basis for agentic development: build → run → inspect output → fix.

    IMPORTANT: IP-Symcon captures the script's OUTPUT, i.e. what it ``echo``/``print``s —
    a top-level PHP ``return`` is NOT captured (it comes back empty). So have the script
    ``echo`` its result. Optional 'parameters' are passed to the script and are available
    there as $_IPS['key']. Side effects depend entirely on the script.
    Returns JSON {script_id, output, ok}.
    """
    if not _write_enabled(params.instance):
        return WRITE_DISABLED_MSG
    try:
        output = await _client(params.instance).call("IPS_RunScriptWaitEx", [params.script_id, params.parameters])
        return _dumps({"script_id": params.script_id, "output": output, "ok": True})
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


@mcp.tool(
    name="ips_set_script_content",
    annotations={"title": "Replace script PHP source", "readOnlyHint": False, "destructiveHint": True,
                 "idempotentHint": True, "openWorldHint": True},
)
async def ips_set_script_content(params: SetScriptContentInput) -> str:
    """Replace the full PHP source of an existing script (IPS_SetScriptContent). Requires IPS_ENABLE_WRITE.

    This overwrites the script entirely — read the current source first with ips_get_script_content.
    Returns JSON {script_id, bytes_written, ok}.
    """
    if not _write_enabled(params.instance):
        return WRITE_DISABLED_MSG
    try:
        await _client(params.instance).call("IPS_SetScriptContent", [params.script_id, params.content])
        return _dumps({"script_id": params.script_id, "bytes_written": len(params.content), "ok": True})
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


@mcp.tool(
    name="ips_create_script",
    annotations={"title": "Create a new PHP script", "readOnlyHint": False, "destructiveHint": True,
                 "idempotentHint": False, "openWorldHint": True},
)
async def ips_create_script(params: CreateScriptInput) -> str:
    """Create a new PHP script, place it under a parent, name it and set its content. Requires IPS_ENABLE_WRITE.

    Performs IPS_CreateScript(0) → IPS_SetParent → IPS_SetName → IPS_SetScriptContent.
    Returns JSON {script_id, name, parent_id, ok} with the new script's ID.
    """
    if not _write_enabled(params.instance):
        return WRITE_DISABLED_MSG
    try:
        client = _client(params.instance)
        new_id = await client.call("IPS_CreateScript", [0])  # 0 = PHP script
        await client.call("IPS_SetParent", [new_id, params.parent_id])
        await client.call("IPS_SetName", [new_id, params.name])
        if params.content:
            await client.call("IPS_SetScriptContent", [new_id, params.content])
        return _dumps({"script_id": new_id, "name": params.name, "parent_id": params.parent_id, "ok": True})
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


@mcp.tool(
    name="ips_create_category",
    annotations={"title": "Create a category", "readOnlyHint": False, "destructiveHint": True,
                 "idempotentHint": False, "openWorldHint": True},
)
async def ips_create_category(params: CreateCategoryInput) -> str:
    """Create a category to structure the object tree (IPS_CreateCategory). Requires IPS_ENABLE_WRITE.

    Performs IPS_CreateCategory → IPS_SetParent → IPS_SetName.
    Returns JSON {category_id, name, parent_id, ok}.
    """
    if not _write_enabled(params.instance):
        return WRITE_DISABLED_MSG
    try:
        client = _client(params.instance)
        new_id = await client.call("IPS_CreateCategory", [])
        await client.call("IPS_SetParent", [new_id, params.parent_id])
        await client.call("IPS_SetName", [new_id, params.name])
        return _dumps({"category_id": new_id, "name": params.name, "parent_id": params.parent_id, "ok": True})
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


@mcp.tool(
    name="ips_create_variable",
    annotations={"title": "Create a variable", "readOnlyHint": False, "destructiveHint": True,
                 "idempotentHint": False, "openWorldHint": True},
)
async def ips_create_variable(params: CreateVariableInput) -> str:
    """Create a typed variable (IPS_CreateVariable). Requires IPS_ENABLE_WRITE.

    Performs IPS_CreateVariable(type) → IPS_SetParent → IPS_SetName, and attaches a profile
    via IPS_SetVariableCustomProfile when 'profile' is given. 'variable_type' is one of
    boolean/integer/float/string. Returns JSON {variable_id, name, parent_id, type, profile, ok}.
    """
    if not _write_enabled(params.instance):
        return WRITE_DISABLED_MSG
    try:
        client = _client(params.instance)
        new_id = await client.call("IPS_CreateVariable", [VAR_TYPE_TO_INT[params.variable_type]])
        await client.call("IPS_SetParent", [new_id, params.parent_id])
        await client.call("IPS_SetName", [new_id, params.name])
        if params.profile:
            await client.call("IPS_SetVariableCustomProfile", [new_id, params.profile])
        return _dumps({
            "variable_id": new_id, "name": params.name, "parent_id": params.parent_id,
            "type": params.variable_type, "profile": params.profile or None, "ok": True,
        })
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


@mcp.tool(
    name="ips_create_event",
    annotations={"title": "Create an event", "readOnlyHint": False, "destructiveHint": True,
                 "idempotentHint": False, "openWorldHint": True},
)
async def ips_create_event(params: CreateEventInput) -> str:
    """Create an event shell (IPS_CreateEvent). Requires IPS_ENABLE_WRITE.

    Performs IPS_CreateEvent(type) → IPS_SetParent → IPS_SetName → IPS_SetEventActive.
    'event_type' is triggered/cyclic/weekly. The detailed trigger/cyclic/schedule config is
    set afterwards via ips_call (e.g. IPS_SetEventCyclic, IPS_SetEventTrigger). Returns JSON
    {event_id, name, parent_id, type, active, ok}.
    """
    if not _write_enabled(params.instance):
        return WRITE_DISABLED_MSG
    try:
        client = _client(params.instance)
        new_id = await client.call("IPS_CreateEvent", [EVENT_TYPE_TO_INT[params.event_type]])
        await client.call("IPS_SetParent", [new_id, params.parent_id])
        await client.call("IPS_SetName", [new_id, params.name])
        await client.call("IPS_SetEventActive", [new_id, params.active])
        return _dumps({
            "event_id": new_id, "name": params.name, "parent_id": params.parent_id,
            "type": params.event_type, "active": params.active, "ok": True,
        })
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


def _var_type_int(vt: Any) -> int:
    """Map an exported variable_type ('Float'/'Boolean'/...) or raw int to IP-Symcon's type int."""
    return vt if isinstance(vt, int) else VAR_TYPE_TO_INT[str(vt).lower()]


_IMPORT_SKIP_REASON = (
    "not mechanically importable — needs the migration skill "
    "(instance/event/link config + reference remapping)"
)


async def _import_node(
    client: IPSClient, node: dict[str, Any], parent_id: int,
    id_map: dict[str, int], skipped: list[dict[str, Any]],
) -> int | None:
    """Recreate one node under parent_id, record old→new in id_map, recurse into children.

    Handles the deterministic types (category/variable/script). Instances, events and links are
    recorded under ``skipped`` (they need config + reference remapping → the migration skill); their
    children are still imported under the same parent so a skipped node never orphans a subtree.
    """
    otype = node.get("type")
    old_id = node.get("id")
    name = node.get("name") or ""
    new_id: int | None
    if otype == "Category":
        new_id = await client.call("IPS_CreateCategory", [])
        await client.call("IPS_SetParent", [new_id, parent_id])
        await client.call("IPS_SetName", [new_id, name])
    elif otype == "Variable":
        new_id = await client.call("IPS_CreateVariable", [_var_type_int(node.get("variable_type"))])
        await client.call("IPS_SetParent", [new_id, parent_id])
        await client.call("IPS_SetName", [new_id, name])
        if node.get("profile"):
            await client.call("IPS_SetVariableCustomProfile", [new_id, node["profile"]])
        if node.get("value") is not None:
            await client.call("SetValue", [new_id, node["value"]])
    elif otype == "Script":
        new_id = await client.call("IPS_CreateScript", [0])
        await client.call("IPS_SetParent", [new_id, parent_id])
        await client.call("IPS_SetName", [new_id, name])
        if node.get("content"):
            await client.call("IPS_SetScriptContent", [new_id, node["content"]])
    else:
        skipped.append({"id": old_id, "type": otype, "reason": _IMPORT_SKIP_REASON})
        new_id = None
    if new_id is not None and old_id is not None:
        id_map[str(old_id)] = new_id
    child_parent = new_id if new_id is not None else parent_id
    for child in node.get("children") or []:
        await _import_node(client, child, child_parent, id_map, skipped)
    return new_id


@mcp.tool(
    name="ips_import_subtree",
    annotations={"title": "Recreate a subtree from exported JSON", "readOnlyHint": False,
                 "destructiveHint": True, "idempotentHint": False, "openWorldHint": True},
)
async def ips_import_subtree(params: ImportSubtreeInput) -> str:
    """Mechanically recreate a subtree (from ips_export_subtree JSON) under a target parent. Requires IPS_ENABLE_WRITE.

    The deterministic half of a migration: recreates categories, variables (type, profile, value)
    and scripts (content), re-parenting each child under its newly created parent, and returns an
    old→new ID map. Instances, events and links are NOT recreated here — they need configuration and
    reference remapping, which is the agentic Migration skill's job; they are reported under
    'skipped' (with their original IDs) so the follow-up can wire them up using the id_map.
    Returns JSON {root_id, id_map, created, skipped, ok}.
    """
    if not _write_enabled(params.instance):
        return WRITE_DISABLED_MSG
    try:
        client = _client(params.instance)
        id_map: dict[str, int] = {}
        skipped: list[dict[str, Any]] = []
        root_new = await _import_node(client, params.tree, params.target_parent_id, id_map, skipped)
        return _dumps({"root_id": root_new, "id_map": id_map, "created": len(id_map),
                       "skipped": skipped, "ok": True})
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


@mcp.tool(
    name="ips_call_read",
    annotations={"title": "Call a reading IP-Symcon function", "readOnlyHint": True,
                 "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
)
async def ips_call_read(params: CallInput) -> str:
    """Generic READ gateway: call any reading IP-Symcon function by name. No write gate.

    Covers what no dedicated tool exposes — IPS_GetEvent, AC_GetLoggedValues,
    IPS_GetScriptList, IPS_GetLocation, MC_GetModuleList and the rest of the documented
    reading API. Params is the positional argument list. Returns JSON {method, result}.

    Anything that is not a reading core function is refused here, whatever the write gate
    says; use ips_call for those. The split exists because permission rules match on the
    tool name and never on an argument: one gateway for both jobs would force the same
    prompt on IPS_GetLocation and IPS_DeleteObject.
    """
    if not _is_read_only_method(params.method):
        return READ_GATEWAY_REFUSED_MSG.format(method=params.method)
    try:
        result = await _client(params.instance).call(params.method, params.params)
        return _dumps({"method": params.method, "result": result})
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


@mcp.tool(
    name="ips_call",
    annotations={"title": "Call any IP-Symcon function", "readOnlyHint": False, "destructiveHint": True,
                 "idempotentHint": False, "openWorldHint": True},
)
async def ips_call(params: CallInput) -> str:
    """Generic gateway: call any IP-Symcon JSON-RPC function by name. Requires IPS_ENABLE_WRITE.

    Use this for full API coverage when no dedicated tool exists — e.g. IPS_CreateVariable,
    IPS_CreateEvent, IPS_CreateInstance, IPS_SetEventActive. Params is the positional argument
    list for the function. Returns JSON {method, result}.

    For reading functions use ips_call_read instead — it needs no write gate, and keeping the
    two apart is what lets a permission rule tell a read from a delete. Permission rules match
    on the tool name, never on an argument.
    """
    if not _write_enabled(params.instance):
        return WRITE_DISABLED_MSG
    try:
        result = await _client(params.instance).call(params.method, params.params)
        return _dumps({"method": params.method, "result": result})
    except Exception as e:  # noqa: BLE001
        return _handle_error(e)


def main() -> None:
    """Entry point — runs the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
