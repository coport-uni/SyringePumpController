# LearnedPatterns.md

> Patterns extracted from [ToDo.md](ToDo.md) Completed items. Consult the relevant sections before drafting new ToDo entries. Append new patterns after each task completes.
>
> Last updated: 2026-05-15
> Total patterns: 7
>
> Provenance format: `(from ToDo#N)` where N is the 1-based index of the top-level `##` section in `ToDo.md` at the time of extraction. Patterns extracted from design rather than from completed work use `(from DESIGN.md §N)` until a corresponding ToDo item lands.

---

## §1. Recurring Issues

*(none yet — populate as failure modes recur during implementation)*

---

## §2. Solved Gotchas

### G1. CH340 USB-serial bridge drops DTR low on `open()`

- **Problem**: A bare `serial.Serial(port=…)` against the EUSB-30 dongle toggles DTR low at the moment the port opens. The SY-01B ignores DTR/RTS, but any peripheral wired into the dongle's DB-9 control pins could glitch on the transition.
- **Cause**: The Linux `ch341.ko` driver and many CH340 driver implementations on other platforms assert DTR=low on open by default; the chip echoes the line state on the RS-232 side.
- **Fix**: Open with `dsrdtr=False, rtscts=False, xonxoff=False`, and explicitly drop both DTR and RTS after the handle exists. Documented in [DESIGN.md §4.1](DESIGN.md#41-usb-serial-bridge-wch-ch340).
- **Rule**: Never open a CH340-based dongle without first neutralizing DTR/RTS, even when the protocol itself does not use them. (from DESIGN.md §4.1)

### G2. Firmware locks to the first ASCII variant it sees per power cycle

- **Problem**: Mixing DT and OEM ASCII frames in the same session corrupts state — the second variant gets rejected with no clean recovery short of a power cycle.
- **Cause**: The SY-01B auto-detects ASCII variant on the first command after boot and refuses any other variant until the next power cycle (per [SY01BE.pdf](SY01BE.pdf) §6).
- **Fix**: Pick one variant (this project uses DT) and enforce it at the transport layer. Don't build a "transport-agnostic" frame builder that could accidentally emit the other variant.
- **Rule**: Treat ASCII-variant choice as a project-wide constant, not a per-command parameter. A "switch protocol mid-session" feature is a footgun, not a feature. (from DESIGN.md §1)

### G3. `bytearray[i:j]` is a `bytearray`, not `bytes` — mypy catches the slip

- **Problem**: `while end < len(buf) and buf[end:end+1] in (b"\r", b"\n"):` mypy-failed with `comparison-overlap` because `buf` was `bytearray` and the slice is `bytearray`, not `bytes`; tuple membership against `bytes` literals never holds.
- **Cause**: bytes/bytearray are distinct types under mypy strict, even though they compare equal at runtime in many situations.
- **Fix**: Compare integer bytes: `buf[end] in (0x0D, 0x0A)`. Simpler, faster, and types correctly.
- **Rule**: When walking a `bytearray` read buffer, index it for single-byte comparisons; slice it only when you need a sub-buffer. (from ToDo#0, ToDo#4)

### G4. Frozen + slots dataclass has no `__dict__` — use `dataclasses.replace()`

- **Problem**: Tried to merge CLI overrides into a TOML-loaded `PumpConfig` with `PumpConfig(**{**cfg.__dict__, **overrides})`. mypy ignored the type:ignore as "unused" because `cfg.__dict__` would raise `AttributeError` at runtime anyway (slots).
- **Cause**: `@dataclass(frozen=True, slots=True)` instances have no `__dict__`. Copy-with-overrides is `dataclasses.replace(cfg, **overrides)`, which is also more readable.
- **Fix**: `cfg = dataclasses.replace(cfg, **overrides)` in `cli/diagnose.py`.
- **Rule**: For frozen dataclasses, the supported "modify a field" idiom is `dataclasses.replace()`, never `__dict__` reconstruction. (from ToDo#7)

---

## §3. Library Quirks

### Q1. pyserial 3.x dropped the `setDTR()`/`setRTS()` methods

- **Problem**: `port.setDTR(False)` / `port.setRTS(False)` worked at runtime but mypy reported `"Serial" has no attribute "setDTR"` with `types-pyserial` 3.5.x stubs.
- **Cause**: pyserial 3.x exposes DTR/RTS as **properties** (`port.dtr = False`, `port.rts = False`). The old camelCase methods are still around for compatibility but are not in the type stubs, and using them would be deprecated.
- **Fix**: Use the property setters: `port.dtr = False; port.rts = False`. Matches the documented pyserial 3.x API and types correctly.
- **Rule**: When neutralizing CH340 DTR/RTS on open, use the property syntax (`port.dtr = False`), never the legacy `setDTR()` method. (from ToDo#4)

---

## §4. Workflow Lessons

### W1. Always run `diagnose()` before `initialize()` on a freshly plugged pump

- **Lesson**: `Z` (init) mechanically homes the plunger. If the serial link is mis-wired, the rotary address switch is set wrong, or the wrong ASCII variant is locked in, a blind `ZR` as the first command can slam the plunger into a hard stop or a closed valve. The diagnostic stage (echo `Q`, `?76`, `*`, `?6`, `?`) confirms communication, addressing, and power *without* moving anything.
- **Rule**: Always call `pump.diagnose()` first, inspect the `DiagnosticsReport`, and only call `pump.initialize()` after the report's `ok_to_initialize` is true. Never auto-init from `Pump.open()`. Document this order in every example and README snippet. (from DESIGN.md §7)

### W2. HIL tests are read-only — never move the plunger or valve from automation

- **Lesson**: Hardware-in-the-loop scripts that drive the real pump must restrict themselves to side-effect-free queries (firmware/build via `?76`, serial number if exposed, supply voltage `*`, status `Q`, valve `?6`, plunger position `?`). Motion testing is a separate, human-supervised activity on the bench. An automated script that moves a syringe risks damaging the plunger, the valve, or whatever fluid line is connected — and is the wrong abstraction for proving "the host can talk to the pump."
- **Rule**: Every HIL-tier test and `examples/hil_*.py` script proves identity, not motion. The `sy01b-diagnose` CLI must refuse to emit `R`/`Z`/`Y`/`W` by code, not by convention. (from DESIGN.md §10.1)

### W3. Use specific identity commands (`?23`, `?202`), not the broad config dump (`?76`)

- **Lesson**: It is tempting to read everything off `?76` (pump configuration) and parse fields out of it. The manual exposes **dedicated** commands for the two identity fields that matter most: `?23` (or `&`) returns the firmware/software version string, and `?202` returns the unique device serial number. Querying these directly is more reliable than parsing a multi-field config blob whose layout differs across firmware revisions.
- **Rule**: For identity verification, prefer single-purpose query commands over multi-field dumps. `?76` is useful for log context, but `?23` and `?202` are the source of truth for version and serial number respectively. (from ToDo#7)

### W4. Defensive test asserting "method is absent" guards future scope creep

- **Lesson**: The read-only commit shipped a `TestNoMotionCommandsExposed` class that asserts `not hasattr(pump, "initialize")`, `aspirate_uL`, `abort`. Looks weird (you don't usually test for absence), but it caught one local-branch experiment that prematurely added an `initialize()` method and would have shipped if not for this guard.
- **Rule**: When a public API is intentionally narrow at a milestone, add tests that assert the *negative* — "this method does NOT exist yet" — so accidental additions land as test failures rather than as silent feature creep. (from ToDo#6)

---

## §5. Environment Specifics

*(none yet — populate when a host- or OS-specific behavior bites)*

---

## §99. Uncategorized

*(none yet)*
