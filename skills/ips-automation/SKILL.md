---
name: ips-automation
description: >-
  Design and build a NEW IP-Symcon automation: thresholds, state transitions, adjustable settings, verification. Triggers: "Automation bauen", "Zeitschaltung", "Schwellwert", "Standby abschalten".
---

# IP-Symcon — designing an automation

Build an automation that **still works in six months** and whose state you can **see and
prove**. The `ipsymcon` skill covers *how to call things safely*; this one covers *what to
build* — the design decisions where automations silently fail.

**REQUIRED BACKGROUND:** the `ipsymcon` skill — same MCP, same *plan-before-you-touch* rule,
same ID resolution. This is design guidance on top, not a replacement.

Full procedure, patterns and the failure catalogue: [references/workflow.md](references/workflow.md).

## The five rules that prevent silent failure

1. **Every tunable is a variable, never a literal.** Times, thresholds, delays, windows —
   each gets its own variable with a profile (range + unit) **and an action script**, so it
   is adjustable from the visualisation. A threshold hardcoded in a script is a threshold
   nobody will ever tune.

2. **Pick thresholds from the spread, not from the means.** Look at min/max of *both* states
   before choosing. Two averages 20 apart can still overlap at the edges; the automation then
   flaps. Decide on the gap between the **ranges**.

3. **A sliding window is blind at every state change.** Any "max/avg over the last N minutes"
   still describes the *old* state right after a switch. Handle transitions explicitly —
   a grace period after switching on, and an authoritative signal (the switch command itself)
   rather than the look-back for "is it on?".

4. **Mirror a working example; never trust the API doc alone.** Before configuring an event,
   read the config of an event **that demonstrably runs** in this system and copy its field
   combination. Then verify with `NextRun` — *active* is not *scheduled*.

5. **Prove it at the Ist, end to end.** Trigger the real path and read the real result. A
   config that looks right is not evidence. And **announce live switching** whenever the
   device might be in use.

## Never

- Never leave scripts, action helpers and timestamps visible in the visualisation — hide the
  machinery, show only what is operated or read.
- Never build a condition on a variable without checking `VariableUpdated` first: IPS carries
  the last value forward forever, so a dead source looks like a stable reading.
- Never poll a device with an exclusive connection while separately probing it — your own
  diagnostics become the fault you then explain.
