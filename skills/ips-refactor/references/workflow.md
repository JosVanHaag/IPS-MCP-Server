# IPS refactoring — workflow

Restructure working parts without changing behaviour. Read-only by default; every write goes
through a plan the human approves first.

---

## Phase 0 — Freeze the behaviour (read-only, MANDATORY)

Refactoring without a before/after comparison is indistinguishable from breaking things.

1. **Snapshot** every variable in the target branch: `ips_snapshot_variables`. Keep the result.
2. **Record the schedule**: for every event in the branch, note `EventActive`, `LastRun`,
   `NextRun`. These must be identical afterwards (except `LastRun` advancing).
3. **Note the archive status** of each variable (`AC_GetLoggingStatus`). Losing history during
   a restructure is the most common silent damage.

## Phase 1 — Map the branch (read-only)

Walk the subtree and record for every object: type, name, ID, value, `VariableUpdated`, archive
status, and for links their target. Produce a table, not prose.

**What to look for:**

| Finding | Why it matters |
|---|---|
| Two variables, same value | One is usually archived, the other not — pick the archived one |
| `Unnamed Object` | A link nobody named; find its target and name it after that |
| Values dated `1970-01-01` | Never filled — candidates, but confirm before assuming |
| Many links flat in one category | Grouping candidate |
| Numbers inside script bodies | Extraction candidates |
| Objects with no incoming reference | Note them — but removal belongs to `ips-cleanup` |

**Classify honestly into three buckets:** *productive* · *structurally messy but working* ·
*possibly dead*. Only the middle bucket is this skill's business. Do not silently widen scope.

## Phase 2 — Dependency scan before any rename or move

Use the **three-vector scan** from `ips-cleanup` — a rename breaks the same things a delete
does, plus one more:

1. **ID references** — search all script contents for the numeric ID.
2. **Links** — every link whose `TargetID` is the object.
3. **Events** — every event triggered by the variable, and every event whose script is affected.
4. **⚠️ Name references** — `IPS_GetObjectIDByName(...)` resolves by **name**. Grep the scripts
   for the *name string* too. This is the vector a delete-oriented scan misses, and it is the
   one that bites on renames.

## Phase 3 — Plan and approve

Present the change as a table with **before → after** for every single operation. Group by kind
(rename / move / consolidate / extract / hide) so the human can approve or reject per group
instead of all-or-nothing.

State explicitly what will **not** change: values, schedules, archive membership.

## Phase 4 — Execute in this order

**The order is not cosmetic — it keeps the system working at every intermediate step.**

1. **Create** new structure (categories, extracted variables with profile + action).
2. **Repoint** references to the new targets — links, scripts, event triggers.
3. **Verify** the system still works with both old and new in place.
4. **Match the visibility of the siblings** (`IPS_SetHidden` only if that is the house
   convention — check, do not assume; see the `ipsymcon` skill, "Creating a script").
5. **Set positions** (`IPS_SetPosition`) so the visualisation has a deliberate order.
6. **Retire** the now-unreferenced leftovers — rename with a `ZZ_` prefix and hide them.
   **Deleting is a separate pass and belongs to `ips-cleanup`**, after a quarantine period.

> **Why retire instead of delete:** if the dependency scan missed a reference, a renamed and
> hidden object still resolves by ID and the system keeps running. A deleted one does not.

## Phase 5 — Prove nothing changed

1. `ips_diff_variables` against the Phase-0 snapshot. **Expect an empty diff** apart from
   values that legitimately move on their own (live measurements).
2. Compare every event's `EventActive` and `NextRun` against Phase 0.
3. Confirm archive status is unchanged for every variable that had it.
4. Only then report — and report the diff, not the intention.

---

## Extracting hardcoded values

The most valuable refactoring in an automation, and the one with a specific trap:

1. Create the variable **with a profile** (range, step, unit) — a raw number without unit is
   half the value.
2. Assign an **action script** or it is a display, not a setting.
3. Replace the literal in the script with `GetValue($id)`.
4. **Set the variable to exactly the previous literal** before switching over, so behaviour is
   provably unchanged.
5. Re-verify: the automation must behave as before, and the value must now be adjustable.

## Common mistakes

- **Refactoring and fixing in one step.** Then the diff proves nothing. Note the defect, finish
  the structure, fix afterwards.
- **Consolidating duplicates by deleting first.** Repoint, run, remove — in that order.
- **Forgetting the archive.** A new variable without `AC_SetLoggingStatus` silently drops the
  history the old one had.
- **Renaming without checking name-based lookups.** See Phase 2, vector 4.
- **Reordering without `IPS_SetPosition`.** IPS keeps its own order; grouping without positions
  looks arbitrary in the visualisation.
