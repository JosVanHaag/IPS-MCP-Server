---
name: ips-cleanup
description: >-
  Audit and clean up an IP-Symcon system — recurring errors, red logs, instances in error state, dead objects. Triggers: "Fehler-Audit", "rote Logs", "tote Instanzen entfernen", "IPS-Health-Check".
---

# IP-Symcon cleanup & error review

Find what's broken or dead in an IP-Symcon system and resolve it: surface the instances in an
error state and the dead/orphaned objects, then **fix what's safely fixable and remove what's
truly dead** — without trading old errors for new orphans. Read-only review by default; every
write goes through a plan you approve first.

**REQUIRED BACKGROUND:** the `ipsymcon` skill — same MCP, same *plan-before-you-touch* rule, the
`ips_call` gateway and type-specific deletes. This is cleanup-specific orchestration on top.

## Why it exists (the trap it prevents)

"The logs are always red" is usually a *handful* of broken instances spamming errors, not a
hopeless mess — but **removing a broken instance naively orphans everything connected to it.** A
device connects to its gateway via **ConnectionID**, not as a tree-child — so a children+links
check reports "0 dependencies" and is *dangerously wrong*. (Verified live: a Hue bridge showed 0
children / 0 links, yet had 4 lamps connected via ConnectionID.) This skill always checks **three
vectors** before deleting, and removes bottom-up.

## Relationship to migration

This is **Phase 0 of a migration**: clean the source first, so a greenfield migrate
(`ips-migration` skill) carries a healthy system across instead of re-implementing the cruft. Also
useful standalone as a periodic health check.

## The phases

```
1 Review (read-only)  →  2 Triage (you decide)  →  3 Safe removal (plan-first)
                                                  ↘  4 Fix (apply safe, flag creds)  →  5 Verify
```

Step-by-step, the ready-to-run scan scripts and the delete-order rules: references/workflow.md.

## The non-negotiables

- **Review is read-only.** Phases 1–2 never write. Surfacing problems ≠ changing the system.
- **Look for what still runs, not only for what is red.** An active event on a dead branch keeps
  polling APIs and keeping stale credentials in use while every status reads `102`. Phase 1b.
  Deactivating an event is the cheap, reversible first move — propose it before any removal plan.
- **Three-vector dependency check before ANY instance delete** — children · incoming links ·
  **ConnectionID**. Skipping ConnectionID is exactly how you create new red while removing old red.
- **Plan-first for every write.** Present the full removal cascade / fix set, then STOP for approval.
- **Delete bottom-up, type-specific.** Connected devices before their gateway, children before
  parents; `IPS_DeleteVariable/Category/Script/Event/Instance/Link` — there is no generic delete.
- **Never guess credentials.** API keys, cloud logins, passwords → diagnose and **flag for the
  human**. Apply only what's safe on your own (re-apply config, `IPS_ApplyChanges`, reconnect).
- **Write-gated** (`IPS_ENABLE_WRITE`); an IPS backup is the safety net, the plan is the guardrail.
