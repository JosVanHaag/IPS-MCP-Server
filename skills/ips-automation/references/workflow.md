# IPS automation — design workflow

Procedure, patterns and the failure catalogue. Every failure listed here was hit for real; the
dates are the incidents it came from.

---

## Phase 1 — Understand the signal before designing anything (read-only)

An automation is only as good as the measurement it reacts to. Do this **before** picking any
threshold or interval.

1. **Find the driving variable** and confirm it is archived (`AC_GetLoggingStatus`). Without
   history you cannot choose a threshold, only guess one.
2. **Read `VariableUpdated` on every variable you intend to use.**
   ⚠️ IPS carries the last value forward indefinitely. A dead source reads like a rock-steady
   measurement. *(2026-07-27: an air-conditioner reported a flat 593.1 W for five days — the
   device had been offline the whole time. Almost became "the biggest consumer in the house".)*
3. **Characterise both states** with `AC_GetAggregatedValues`: avg **and min/max** for
   "off/idle" and for "running". You need the spread, not the averages.
4. **Establish what "off" looks like at the source.** Does a switched-off socket report 0, or
   stop reporting? Test it once and write the answer into the script comment.

## Phase 2 — Choose the numbers from the data

**Threshold.** Place it in the gap between the two *ranges*, not between the two means.

> *Real case:* standby averaged 11.5 W, running 252 W. The obvious threshold was 25 W —
> but standby **peaked at 59 W**, so every spike would have reset the timer and the socket
> would never have switched off. Running never dropped below 245 W → **150 W** separates with
> margin on both sides.

**Interval.** Fast enough that the display is not stale, slow enough to stay cheap. One minute
is usually the sweet spot for power-based logic. Note the resolution this gives your history —
that is what you will read back later.

**Delay / follow-up time.** Long enough to survive the longest legitimate pause in the "running"
state; short enough to still save something.

## Phase 3 — Structure: what the user sees, what stays hidden

**Where things go is a house convention — read it off the tree before you place anything.**
Two layouts are common and both are coherent: one category per automation holding everything
(variables, scripts, events), or objects placed semantically — a script under the device it
serves, its variables under that script, and the shared category reserved for cross-cutting
helpers. Look at a comparable automation that already exists and follow it; see
`../../ipsymcon/references/haus-konventionen.md`.

**Visible** — one row per thing that is operated or read:

| Kind | Example |
|---|---|
| State | `Dose` (switchable), `Status` (enum profile: off / idle / running) |
| Live value | a **link** to the source variable — never a copy (a copy needs its own logging and drifts) |
| Master switch | `Automatik aktiv` (bool, `~Switch`, with action) — the off-switch that stops all intervention |
| Every tunable that is really tuned | time, threshold, delay, window — each with a **profile** (range, step, unit) **and an action script**. But see the note on variable budget below: a constant that has never been changed is a `const` in the script, not a variable |

**Hidden** (`IPS_SetHidden`): internal state the user neither operates nor reads — last-run
timestamps, counters, helper flags. Whether *scripts* are hidden is a house convention and
differs between installations: check the siblings and follow them (see the `ipsymcon` skill,
"Creating a script"). Events are never shown in the visualisation anyway.

> ⚠️ **Variables are a budget, not free.** An IP-Symcon licence caps the number of variables,
> and a grown installation can sit close to that cap. Before adding one, ask whether the
> value is *derivable* from something that already exists — a threshold comparison, a sign,
> a fixed vorwarn window. A variable that only restates a constant or a comparison of another
> variable costs a licence slot and buys nothing. Cache an expensive computation, yes;
> materialise a one-line derivation, no.

> **A variable with a profile but no action is a display, not a setting.** Assign an action
> script (`SetValue($_IPS['VARIABLE'], $_IPS['VALUE']);`) or it cannot be changed from the UI.
> One shared action script for all tunables is enough.

**Give the state variables archive logging.** They are the only way to answer "what did it
actually do last night" — and that question always comes.

## Phase 4 — Handle the transitions (this is where automations break)

Any logic based on a **look-back window** — max/avg over the last N minutes — describes the
**old** state immediately after a switch. All three of the following came from one evening's
build:

| Failure | Symptom | Fix |
|---|---|---|
| **Look-back kills a fresh switch-on** | Manually switched on, killed again within a minute — the 30-min window contained only idle values | **Grace period**: stamp the switch-on time, do not evaluate until the delay has elapsed |
| **Look-back keeps reporting "on" after switch-off** | Display claimed "on" for a minute after switching off; even started a bogus grace period | **The switch command is authoritative** for on/off; power only debounces it |
| **Race between two events in the same second** | Scheduled switch-on ran, then the watchdog ran and reset the display, because its window was still all zeros | **Grace window on the command's `VariableChanged`**, so the command wins right after any switch |

**The resulting shape:**

```php
$cmdOn = (strtoupper(trim((string)GetValue($CMD))) === 'ON');   // authoritative
$pMax  = GetValue($P);                                          // debounce: single 0-outliers
$raw   = @AC_GetLoggedValues($ARCH, $P, time()-120, time(), 0);
if (is_array($raw)) foreach ($raw as $x) if ($x['Value'] > $pMax) $pMax = $x['Value'];

$cv    = IPS_GetVariable($CMD);
$fresh = (time() - $cv['VariableChanged']) < 150;               // just switched → trust command

$on    = $cmdOn && ($fresh || $pMax > 0);
```

**Two more sequencing rules:**

- **Update state before the early returns.** If the "automation off" check comes first, the
  display freezes exactly when someone takes over manually. State first, intervention after.
- **A single script beats a trigger plus a timer.** Instead of tracking elapsed time in a
  variable, ask the archive: *"what was the maximum over the last N minutes?"* That removes one
  variable and one event.

## Phase 5 — Build, then prove it

**Events: mirror, don't guess.** Before configuring, read the config of an event that
**demonstrably runs** in this system and copy the field combination.

> *Real case:* a daily event configured with `CyclicDateType = 1` looked correct and reported
> `EventActive = 1` — but `NextRun` stayed **0** and it would never have fired. The working
> events in the same system all use `DateType = 2, DateValue = 1`. Copying that produced a
> `NextRun` immediately.

**`NextRun` is the proof that an event is scheduled. `EventActive` is not.** Check it after
every event change.

**Verify end to end at the Ist.** Trigger the real path, read the real result, quote the
timestamps. A configuration that looks right is not evidence.

⚠️ **Announce live switching whenever the device might be in use.** Cutting power to a
projector at 20:05 while its owner sits in front of it is a correct test at a wrong moment.
Daytime on a standby device: fine. Evening on a running one: ask first.

⚠️ **Never probe a device with an exclusive connection while its poller is running.** ModBus
TCP, serial devices and single-session APIs allow one connection. Parallel diagnostics steal
the slot and produce symptoms you will then explain incorrectly.
*(2026-07-27: twelve ModBus probes against a Phoenix Contact controller produced an apparent
"off-by-one" in the register map. The give-away: the **same** address answered in one pass and
not in the next — a register problem is not time-dependent. The fix had been correct all along;
stopping the probing was the whole solution.)*

---

## Checklist before calling it done

- [ ] Every tunable is a variable — with profile **and** action
- [ ] Threshold sits between the **ranges** of both states, not the means
- [ ] Transitions handled: grace period after switching on, command authoritative for on/off
- [ ] State update happens **before** any early return
- [ ] State variables are archived
- [ ] Internal state hidden; script visibility follows the siblings, not a guess
- [ ] No variable added that is derivable from one that already exists (licence budget)
- [ ] `NextRun` confirms every event is scheduled
- [ ] End-to-end verified at the Ist, with timestamps
- [ ] `VariableUpdated` checked on every source variable
- [ ] The user knows what the manual override is and that it survives the automation
