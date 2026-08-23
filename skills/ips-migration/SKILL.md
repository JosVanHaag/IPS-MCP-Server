---
name: ips-migration
description: >-
  Migrate an IP-Symcon object subtree between instances, or verify such a migration. Triggers: "IPS umziehen", "IP-Symcon auf neuen Rechner", "Subtree auf andere Instanz", "Config migrieren".
---

# IP-Symcon subtree migration

Migrate an IP-Symcon object subtree from a **source** instance to a **target** instance:
carry structure, logic and module instances across, and **rewire every ID reference** so the
copy actually works on the new system. This is the agentic counterpart to the deterministic
`ips_export_subtree` / `ips_import_subtree` primitives — it orchestrates them, then does the
reasoning the primitives deliberately don't (instances, references, flagging).

**REQUIRED BACKGROUND:** the `ipsymcon` skill — same MCP, same *plan-before-you-touch* rule, and
the same optional `instance` parameter on every tool (read from `source`, write to `target`).
This skill is migration-specific orchestration layered on top of it.

## What it does — and deliberately does not (v1)

**Does:** *greenfield* migration (target empty/fresh) — recreate categories, variables, scripts,
events, links and instances under a target parent, and remap every ID reference (script object
IDs, link targets, event triggers, instance configuration) through one old→new **id_map**.
Plan-first, write-gated, verified by re-export.

**Does not:**
- **Semantic matching** onto objects that already exist on the target (matching an old MQTT
  instance to an existing one) — that is a *later* skill. v1 assumes greenfield.
- **Evaluation / refactoring** of the config — also a *later* skill. v1 only **flags** smells;
  it never "improves" anything silently.
- **Install modules** on the target — it *checks* that required modules are present and **flags**
  the missing ones (a missing module blocks its instances).

**Faithful-but-flagged, not blind 1:1.** v1 transports correctly and surfaces what looks wrong;
deciding what to *fix* is the human's (or the later refactoring skill's) call.

## Core shape

```
read (source)  →  plan + flag (NO writes)  →  approve  →  two-pass write  →  verify
                                                          (1: create all
                                                           2: wire references)
```

The full step-by-step, the exact `ips_call` recipes, the flag catalogue and the plan/report
templates live in **[references/workflow.md](references/workflow.md)** — whose second half is a
**field-lessons section from the first real parallel migration (2026-08-23)**: build the receiver
before debugging the sender, why a migration card ages faster than the system, finding consumers
via scripts *and* events *and* links, and the safety-interlock rule (never rebuild an automation
whose interlock has no source on the target). Read it before planning — it covers the failures the
recipe above does not. The IP-Symcon function
signatures are in the ipsymcon skill's **[ips-functions.md](../ipsymcon/references/ips-functions.md)**.

## The non-negotiables

- **Plan-first.** Present the whole plan — objects to create, references to rewrite, every flag —
  and **STOP for approval before any write.** Same gate as the ipsymcon skill; IP-Symcon runs a
  real house.
- **Two-pass ordering.** Create *every* object first and build the complete id_map; only **then**
  wire references. A reference can point to an object that gets created later — one pass cannot work.
- **Never guess an unmapped ID.** A reference pointing *outside* the exported subtree has no entry
  in the id_map → leave it as-is and **flag it** for manual fixing. Do not invent a target.
- **Script-ID rewrites are reasoning, not search-replace.** Not every integer in PHP is an object
  ID. Only remap IDs inside known ID-bearing calls (`GetValue(<id>)`, `SetValue(<id>,…)`, etc.),
  and show every proposed script edit in the plan for review.
- **Write-gated.** Needs `IPS_ENABLE_WRITE`. Run against the **target** test/staging first; an
  IP-Symcon backup is the safety net, the plan is the primary guardrail.
