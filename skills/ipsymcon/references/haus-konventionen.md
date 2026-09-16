# House conventions

Conventions of *this* installation. They are not properties of IP-Symcon and not universal —
another house may do the opposite and be equally right. They live in one file so they can be
read, changed or dropped as a unit, and so that a change meant for upstream never drags them
along by accident.

Everything here is stated generically: no object ids, no group addresses, no device or room
names. The concrete inventory of this installation lives outside the repository.

## Objects

**A script exists twice — as an object in the tree and as a file on disk.** Both halves have
to be created, in this order: object first (`ips_create_script`), code second. Never write a
file into the scripts directory for which no object exists — it is invisible in the UI, never
runs, and nothing reports it. IP-Symcon writes the file itself when the object is created, with
a short stub; the stub gets replaced, not pre-empted by a file of your own.

**Place objects semantically, not in a dumping ground.** A script that clearly serves one
device belongs under that device's instance. The variables a script owns and maintains belong
under that script. The shared scripts category is for cross-cutting helpers only — class
files, maintenance runners, calculations that belong to no single device.

**Scripts stay visible.** In this house every script is visible in the tree; that is
deliberate, not an oversight. Do not hide a newly created script. Internal state variables
(last-run timestamps, counters, helper flags) *are* hidden.

**Read back what you created.** `ips_get_object` for parent and flags, `ips_get_script` for the
file the object points at, `ips_get_event` for a timer's interval. "The call returned true" is
not the same as "the setting took effect" — several IP-Symcon setters report success while the
value silently did not land.

## Variables

**Variables are a licence budget.** The licence caps how many exist, and this installation runs
close enough to the cap that it matters. Before adding one, ask whether the value is derivable
from a variable that already exists — a threshold comparison, a sign, a fixed window. A
variable that only restates a comparison costs a slot and carries no information of its own.
Caching an expensive computation is a fair reason; materialising a one-line derivation is not.

**A custom profile beats the variable's own profile.** When both are set, the custom one is in
effect — report and reason with that one.

**A variable with a custom action script must set its own value.** IP-Symcon calls the script
on `RequestAction` but does **not** write the variable; the script has to call `SetValue`
itself. Forget it and the variable keeps its old value while everything reports success.

## Code

**Comments in German, ASCII only** — in PHP, Python and Bash alike, including string literals
in code. Prose outside code (documentation, form captions, commit messages) uses real umlauts.

**Credentials never go into a script.** They belong in an unversioned include with restrictive
permissions. The scripts directory is a git repository; a literal in the code lands in the
history and stays there.

**The scripts directory is under version control.** Writing script content through the MCP
server changes the file on disk — the commit discipline still applies, and a script change is
not finished until it is committed.
