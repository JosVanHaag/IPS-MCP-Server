---
name: ipsymcon
description: >-
  Inspect and modify an IP-Symcon system via the ipsymcon MCP — object tree, variables, PHP scripts, events, automations. Use for any Symcon/Hausautomation request. Changes are always planned first.
---

# IP-Symcon development

This skill turns you into a careful developer *inside* a live IP-Symcon home automation.
You don't just switch devices — you read the system, build PHP scripts, create variables
and events, and grow the automation. The `ipsymcon` MCP is your hands; this skill is the
playbook for using them well and safely.

- **How to carry out a change** (step-by-step workflows, plan & report templates,
  pitfalls): [references/workflow.md](references/workflow.md).
- **`ips_call` function cheat-sheet** (signatures + recipes for event triggers, profiles,
  instances, rename/move/delete): [references/ips-functions.md](references/ips-functions.md).

## The one rule that matters: plan before you touch

IP-Symcon runs a real house — heating, blinds, sockets, sensors. A wrong write can have
physical consequences. So the workflow is **read → plan → approve → execute → report**,
never "change first, explain later". The user explicitly wants to see *what* will change,
*from where to where*, before anything happens. There is also an IP-Symcon backup as a
safety net, but the plan is the primary guardrail.

1. **Start in plan mode** (`EnterPlanMode`). Reading is fine — explore freely.
2. **Resolve and read the current state** of everything you intend to change. IDs are
   opaque integers; never guess them, always resolve from names/tree.
3. **Assemble a plan** naming every change as a concrete operation with a clear
   *before → after* (template in [workflow.md](references/workflow.md)).
4. **Present the plan via `ExitPlanMode`** and stop. Do not call a single write tool yet.
5. **Only after approval**, execute the writes in order, capturing each result.
6. **Report exactly what changed** — every operation performed, with IDs and before/after.
   If a step fails, stop and report; don't push on.

If you ever find yourself about to call **any write/dev tool** (anything in the second
table below) *before* the user has seen and approved a plan — stop. That's the failure
mode this skill exists to prevent.

## The MCP tools (26)

Read tools (always available, safe to use during planning):

| Tool | What it gives you |
|---|---|
| `ips_list_children` | direct children of an object (id/name/type). Start at `object_id=0` for the top level. |
| `ips_get_object` | metadata of any object: type, parent, children, ident. |
| `ips_get_object_tree` | a whole nested subtree `{id,name,type,children}` at once (`max_depth`) — far fewer round-trips than walking level by level. |
| `ips_find_object_by_name` | resolve an object ID from its exact name under a parent. |
| `ips_get_variable` | variable metadata + current value (type, profile, timestamps). |
| `ips_get_value` | just the current value of a variable. |
| `ips_get_variable_by_path` | a variable's value by object **path** (`Räume/Büro/Zustand`) instead of its ID. |
| `ips_get_script_content` | the full PHP source of a script. |
| `ips_snapshot_variables` | capture the current values of several variables (keep it for an after-diff). |
| `ips_diff_variables` | diff a previous snapshot against live values → what changed (verify the effect of a change). |
| `ips_find_objects` | substring search over the **whole** tree, matched against name *and* ident — the first step when only a fragment of the name is known. Use `ips_find_object_by_name` only when you know the exact name and its parent. |
| `ips_get_logged_values` | a variable's archived history, **oldest first**, with `held_seconds` per entry. Checks that the variable is archived at all first — otherwise "not archived" and "no change in this period" both look like an empty list. |
| `ips_call_read` | generic **read** gateway to any reading core function without a dedicated tool — `IPS_GetEvent`, `AC_GetLoggedValues`, `IPS_GetScriptList`, `IPS_GetLocation`, `MC_GetModuleList`. Refuses anything that is not a reading core function, and refuses the readers that carry integration credentials (`IPS_GetConfiguration`, `IPS_GetSnapshot`). |
| `ips_export_subtree` | serialize a subtree to rich JSON for **backup/migration** (variables type+profile+value, script content, event/instance/link detail). |

Write/dev tools (only after an approved plan; gated by `IPS_ENABLE_WRITE`):

| Tool | Effect |
|---|---|
| `ips_set_value` | write a variable value directly (no device action). |
| `ips_request_action` | actuate a device (runs the variable's action) — the correct way to control actuators. |
| `ips_run_script` | execute a script (fire-and-confirm, no output). |
| `ips_run_script_capture` | execute a script **and capture its output** — the build→run→inspect loop (see note). |
| `ips_set_script_content` | **overwrite** a script's full PHP source. |
| `ips_create_script` | create a new PHP script (parent + name + content). |
| `ips_create_category` | create a category (structure the tree). |
| `ips_create_link` | create a link to an existing object — how one value appears in a second place in the tree. A copy would need its own logging and would drift. |
| `ips_create_variable` | create a typed variable (`boolean`/`integer`/`float`/`string`, optional profile). |
| `ips_create_event` | create an event shell (`triggered`/`cyclic`/`weekly`); detailed config via `ips_call`. |
| `ips_import_subtree` | mechanically recreate a subtree from `ips_export_subtree` JSON (categories/variables/scripts) under a target parent → old→new **id_map**; instances/events/links are `skipped` (handled by the `ips-migration` skill). |
| `ips_call` | generic **write** gateway for everything without a dedicated tool (instances, profiles, event triggers, rename, move, delete…). Reading goes through `ips_call_read` — the two are separate because a permission rule matches on the tool name and never on an argument, so one gateway would force the same prompt on `IPS_GetLocation` and `IPS_DeleteObject`. |

> **`ips_run_script_capture` captures the script's OUTPUT** (what it `echo`/`print`s), **not**
> a top-level PHP `return` (that comes back empty). Have the script `echo` its result.

If a write tool returns "Write/dev tools are disabled", `IPS_ENABLE_WRITE` is not set to
true — tell the user, don't try to work around it.

## Targeting a specific instance (multi-instance setups)

If the server is configured with multiple named instances (`IPS_INSTANCES_FILE`), **every
tool takes an optional `instance`** (e.g. `home`, `linux`); omit it for the default. This is
how you read from one system and write to another — e.g. migrating a subtree from an old
IP-Symcon to a new one. Single-instance setups ignore it.

## How IP-Symcon is structured (enough to work confidently)

Everything is one tree of **objects**, each with an integer ID (root = `0`). Object types:

- **Category (0)** — a folder for structure.
- **Instance (1)** — a module instance: a device, a gateway, a configurator, etc.
- **Variable (2)** — a typed value: `0` Boolean, `1` Integer, `2` Float, `3` String. A
  variable may have a **profile** (formatting + range) and an **action** — if it has an
  action, control it with `ips_request_action`, not `ips_set_value`, so the device reacts.
- **Script (3)** — PHP code. The full IP-Symcon PHP API (`GetValue`, `SetValue`,
  `RequestAction`, `IPS_*`) is available inside scripts.
- **Event (4)** — an automation: cyclic (timer), triggered (on a variable change), or
  scheduled (weekly plan). Events run a script or contain their own action.
- **Media (5)** / **Link (6)** — files and tree shortcuts; rarely the target of changes.

`ips_list_children(0)` lists the top level; descend with `ips_list_children` /
`ips_get_object_tree`, or jump to a known name with `ips_find_object_by_name`. Read a
variable's real value before reasoning about it.
