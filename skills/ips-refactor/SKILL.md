---
name: ips-refactor
description: >-
  Restructure WORKING IP-Symcon parts without changing behaviour — duplicates, renaming, grouping, extracting constants. Not ips-cleanup (that removes dead things). Triggers: "Struktur sortieren".
---

# IP-Symcon — refactoring

Restructure what **works** so it stays understandable, without changing behaviour. The
measure of success is unusual: **nothing may change except the structure.** Every value, every
schedule, every automation behaves exactly as before.

**REQUIRED BACKGROUND:** the `ipsymcon` skill (plan-before-you-touch, ID resolution) and the
**three-vector dependency scan** from `ips-cleanup` — a rename or move breaks the same
references a delete does.

**Not this skill:** removing dead objects → `ips-cleanup`. Moving to another instance →
`ips-migration`. Building something new → `ips-automation`.

Full procedure: [references/workflow.md](references/workflow.md).

## The rule that defines refactoring

**Behaviour is frozen; only structure moves.** Before touching anything, snapshot the values
that must stay identical (`ips_snapshot_variables`). After the change, diff them
(`ips_diff_variables`). A refactoring that changes a value is a bug, not an improvement.

## What counts as refactoring here

| Smell | Move |
|---|---|
| Two variables holding the same value, only one archived | Consolidate onto the archived one, repoint references, retire the other |
| `Unnamed Object` links | Name them after what they point at |
| Dozens of links loose in one category | Group into sub-categories, set positions |
| Threshold/time hardcoded in a script | Extract into a variable with profile + action |
| Scripts and helpers visible in the visualisation | Hide the machinery, keep only what is operated or read |
| Three parallel integrations of the same device | Decide which is productive — then this becomes a `ips-cleanup` job for the others |

## Never

- Never rename or move before the dependency scan. Scripts reference **IDs**, but also names
  via `IPS_GetObjectIDByName` — a rename can break code that a pure ID search will not find.
- Never resolve a duplicate by deleting first. Repoint every reference, let it run, remove
  afterwards.
- Never refactor and fix in the same step. If you find something broken mid-refactor, note it
  and finish the structural change first — otherwise the diff no longer proves anything.
