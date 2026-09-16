# IP-Symcon — workflows, templates, pitfalls

The detailed *how*. SKILL.md holds the rule and the tool list; this file is the procedure
you follow once a task is concrete. Every change still goes through **read → plan →
approve → execute → report** (see SKILL.md).

## Workflows

### Inspecting / exploring (read-only)

No plan needed — reading never changes anything. Browse with `ips_list_children` /
`ips_get_object_tree`, read variables and script sources, summarise. This is also always
the *first* phase of any change: resolve the concrete object IDs here so the plan is
precise. Use `ips_get_variable_by_path` when you know the path but not the ID.

### Editing an existing script

1. `ips_get_script_content(script_id)` — read the current source. Edit from the real code,
   never from memory; the current source is your before-state for the diff.
2. Produce the new **full** source (the write tool overwrites the whole script — provide
   the complete file, not a fragment).
3. Plan: show the script's id + name, change as a focused before/after hunk (don't dump
   300 unchanged lines).
4. On approval: `ips_set_script_content(script_id, new_full_content)`.
5. Optional verify: `ips_run_script_capture(script_id)` — but remember it returns what the
   script **`echo`s**, not a `return`.
6. Report: id, name, what changed.

### Creating a script

1. Resolve the parent id by reading the tree — **not necessarily a Category.** Where a
   script belongs is itself a house convention: see `references/haus-konventionen.md`.
2. Plan: "new PHP script `<name>` under `<parent name> (<id>)`, content: <show it>".
3. On approval: `ips_create_script(parent_id, name, content)` → note the new id.
4. **Read it back** with `ips_get_object`: parent as planned, visibility matching the
   siblings — see below.
5. Report the new id.

> ⚠️ **Visibility is a house convention, not a rule of the tool. Read it off the tree.**
> `ips_create_script` creates the object **visible**. Whether that is right depends on the
> installation: in some houses every script is hidden machinery, in others every script is
> deliberately visible. Both are consistent. Guessing is not.
>
> ➡️ **Before creating, check the siblings** (`ips_get_object` on an existing script under
> the target parent) and follow what is already there. The convention is usually written in
> the neighbours.
>
> **Found the hard way, twice, in opposite directions:** once a freshly created script sat
> visible among three hidden siblings and the user had to point it out; once an agent was
> about to hide a new script in an installation where every single script was deliberately
> visible. Same lesson both times — the tree already knows, and it was not read.
>
> After creating, **verify with `ips_get_object` that the flag is what you intended**;
> "the call returned true" is not the same as "the flag is set" (same class of trap as
> `IPS_SetVariableCustomAction` reporting success while a `StepSize 0` profile kept the
> control dead).
>
> **Variables** carry the values the user wants to see and stay **visible** unless they are
> pure internal state (last-run timestamps, counters, helper flags). Events are never shown
> in the visualisation anyway.

### Creating a category / variable / event

Dedicated tools handle the basic create now — prefer them over raw `ips_call`:

- Category → `ips_create_category(parent_id, name)`.
- Variable → `ips_create_variable(parent_id, name, variable_type, profile?)`
  (`variable_type` = `boolean`/`integer`/`float`/`string`).
- Event → `ips_create_event(parent_id, name, event_type, active?)`
  (`event_type` = `triggered`/`cyclic`/`weekly`) — this creates the **shell**; the detailed
  trigger/cyclic/schedule config still goes through `ips_call`
  (`IPS_SetEventTrigger`, `IPS_SetEventCyclic`, `IPS_SetEventScript`) — see
  [ips-functions.md](ips-functions.md).

In the plan, list the create call(s) and any follow-up `ips_call` config with their
parameters and the resulting structure. Events especially: spell out the trigger (which
variable / which interval) and which script they run.

### Controlling a device or setting a value

Even "just turn it on" is a write — it belongs in a (short) plan. Prefer
`ips_request_action` for actuators (it runs the action; `ips_set_value` only writes the
number). Read the current value first so the plan shows `from <current> to <new>`.

### Verifying the effect of a change (snapshot / diff)

When a change should move *other* values (an automation firing, a device reacting):

1. Before: `ips_snapshot_variables([ids…])` — keep the returned `variables` map.
2. Make the change (per its own plan).
3. After: `ips_diff_variables(before=<that map>)` → reports exactly what moved.

This is the build → run → see-what-changed loop; pair it with `ips_run_script_capture`
when developing script logic.

### Backing up a subtree (export)

`ips_export_subtree(root_id, max_depth?)` serializes a whole area to rich JSON
(variables type+profile+value, script content, event/instance/link detail). Read-only —
use it to snapshot an area before a risky change, or to version a configuration. The
restore/adaptation side (recreating + remapping IDs onto another system) is a separate
agentic workflow, not this export.

## Plan format

```
## Plan: <one-line goal>

Affected objects (read from the live system):
- [<id>] <name> (<type>) — <current relevant state>

Changes:
1. <operation> on [<id>] <name>
   from: <current value / current code hunk / "does not exist">
   to:   <new value / new code hunk / new object under [<parent id>] <parent name>>
   via:  <which tool/IPS function>
2. ...

Not touched: <anything nearby you deliberately leave alone, if relevant>
Reversibility: <e.g. "old script source saved below for rollback" / "IPS backup exists" / "exported subtree saved">
```

Then hand control back with `ExitPlanMode` and wait. For script rewrites, paste the
current source into the plan (or save it) so there's an explicit rollback reference.

## Change report format

After executing an approved plan, report what *actually* happened:

```
## Done
- [<id>] <name>: <before> → <after>   ✓
- created [<new id>] <name> under [<parent id>] <parent>   ✓
- <step that failed>: <error>   ✗ (stopped here)
```

Always use the real IDs returned by the tools, not the ones you planned with — creation
may return a different id.

## Things that bite

- **`ips_set_script_content` overwrites the entire script.** Always read it first and write
  back the complete new source.
- **`ips_run_script_capture` returns `echo`/`print` output, not a top-level `return`.**
- **IDs are opaque.** Two objects can share a name in different categories — resolve via the
  tree/parent and confirm the type before acting.
- **`set_value` vs `request_action`.** `set_value` only writes the number; if the variable
  drives a device, it won't move unless you use `request_action`.
- **Deleting (`IPS_DeleteObject` via `ips_call`) is destructive and often cascades** to
  children. Put it in the plan explicitly; prefer disabling/renaming over deleting when
  unsure.
- **One change at a time when it's physical.** Heating/blinds/sockets: small verifiable
  steps over a big batch; check the resulting value after writing.
