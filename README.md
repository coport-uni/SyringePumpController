# SyringePumpController

Python driver for the **Runze SY-01B Smart Syringe Pump** over RS-232 via a CH340-based USB-to-serial bridge (EUSB-30 dongle). Speaks the **DT ASCII** protocol.

Companion to [CLAUDE.md](CLAUDE.md) (protocol/hardware reference) and [DESIGN.md](DESIGN.md) (architecture rationale).

## Status

Pre-alpha (`0.2.0.dev0`). The **read-only commissioning API** is shipped and HIL-verified against a real pump on `/dev/ttyUSB1` (returns software `8.33`, serial `32656`). Motion methods (`initialize`, `aspirate_uL`, `dispense_uL`, `abort`) are **intentionally absent**; the test suite asserts that via `TestNoMotionCommandsExposed`. They land in a later commit with their own HIL-mock test plan.

## If you've never used a syringe pump before

The SY-01B is a programmable precision dispenser. A stepper-driven **plunger** moves a syringe up and down to aspirate or dispense fluid in the nL–mL range; a motor-driven **valve** routes the fluid between an input port, an output port, and (sometimes) a bypass. A host PC drives both over serial.

Before plugging anything in:

- **Power:** 24 V DC, ≥ 1.5 A on the DB-15 header. Never connect or disconnect the pump while powered.
- **Serial:** default 9600 bps, 8N1. The EUSB-30 dongle enumerates as `/dev/ttyUSB0` or `/dev/ttyUSB1` on Linux.
- **Address switch:** 16-position rotary (0–F) on the pump body sets the bus ID — position 0 → address `1`, position 1 → `2`, …, position E → `15`. Must be set **before** power-on. Position F is self-test.
- **Syringe size:** declare the installed syringe volume (µL) in code. It also selects the stall-current operand (see the table in [CLAUDE.md](CLAUDE.md)).
- **Diagnose first, move second.** The `diagnose()` API and `sy01b-diagnose` CLI never emit `R`/`Z`/`Y`/`W` — they verify wiring, address, voltage, and identity without moving anything. Run this before any motion code.

## Communication protocol (DT ASCII)

The SY-01B locks to the first ASCII variant it sees per power cycle (DT vs. OEM). This codebase emits **only DT** frames — no checksum, framed by `\r` and `ETX`.

### Host → pump (request)

```
   /      1        <commands>      \r
  0x2F  '1'..'?'   ASCII string   0x0D
        address    command body    CR
```

- **`/`** — every request starts with slash.
- **Address byte** — integer pump address 1..15 mapped to ASCII via `SyringePumpController.format_address`:
  - 1 → `'1'` (0x31), …, 9 → `'9'`, 10 → `':'` (0x3A), …, 15 → `'?'` (0x3F).
- **Command body** — concatenated ASCII commands. E.g. `IA6000OA0R` = input port → aspirate to step 6000 → output port → dispense to step 0.
- **Execute trigger `R`** — motion commands (`Z`, `Y`, `A`, `P`, `D`, `I`, `O`, …) only run when the body ends in `R`. Without `R` the command sits in the pump's 255-byte buffer waiting for the next `R`. Report/query commands (`Q`, `?23`, `?202`, `*`, …) do not need `R`.
- **`\r`** — single carriage return terminates the frame.

The builder is `SyringePumpController.build_command(address, cmds, *, execute=False)`. Only `execute=True` appends `R`, and the read-only call sites always pass `execute=False`.

> **Codebase safeguard:** the diagnostic path never appends `R`/`Z`/`Y`/`W`. The test `test_diagnose_never_sends_R_or_init_command` inspects every transmitted frame to enforce this.

### Pump → host (reply)

```
   /     0    <status>   <data...>    ETX    \r    \n
  0x2F  '0'   1 byte      ASCII      0x03   0x0D  0x0A
        host
       master
```

- **`/0`** — replies always carry slash + ASCII `'0'` (host = master address). Anything else raises `ProtocolError`.
- **Status byte** — single byte, bit-mapped. Decoded by `SyringePumpController.StatusByte.decode`:

  | Bit | Meaning |
  |---|---|
  | 7 | always `0` |
  | 6 | always `1` (frame identifier) |
  | 5 | `1` = busy, `0` = ready |
  | 4 | reserved |
  | 3..0 | **error code (0 = OK)** |

  Examples: `0x40` = ready + OK, `0x60` = busy + OK, `0x47` = ready + "not initialized" (error 7).

- **Data** — payload depends on the command: `?23` → version string, `?202` → serial number, `*` → supply voltage × 10 (integer), `Q` → empty.
- **ETX (0x03)** — end-of-data. The driver reads until ETX or `reply_timeout_s`.
- **`\r\n`** — trailing CRLF.

### Error codes (status byte bits 3..0)

| Code | Meaning | Recovery |
|---|---|---|
| 0 | OK | — |
| 1 | Init failed | Clear blockage, **must re-init**. Pump rejects everything until cleared. |
| 2 | Invalid command | Send a valid command. No re-init. |
| 3 | Invalid operand | Fix the parameter. No re-init. |
| 7 | Not initialized | Send `Z`/`Y`/`W`. |
| 9 | Plunger overload (backpressure) | **Must re-init** before further moves. |
| 10 | Valve overload | Next valve command auto-homes; repeated → valve needs replacement. |
| 11 | Plunger move blocked (valve in bypass) | Move valve off bypass first. |
| 15 | Command buffer overflow (sent a move while still moving) | Poll `Q` until ready. |

Each code maps to a subclass of `SyringePumpController.Error` (`InitFailedError`, `PlungerOverloadError`, `CommandOverflowError`, …). `SyringePumpController.device_error_for(code)` exposes the mapping.

> **Bus rule:** in serial mode the **only reliable busy/ready signal is `Q`**. Do not trust bit 5 on replies to other commands (see [CLAUDE.md](CLAUDE.md) and the manual).

### Frames you'll see most often

| Sent | Meaning | Reply (example) | Notes |
|---|---|---|---|
| `/1Q\r` | Query pump 1 status | `/0` + status + ETX | Busy/ready + error. **No `R`.** |
| `/1?23\r` | Software version | `/0` + status + `8.33` + ETX | Cheapest connectivity check. |
| `/1?202\r` | Serial number | `/0` + status + `32656` + ETX | Good first roundtrip. |
| `/1*\r` | Supply voltage × 10 | `/0` + status + `240` + ETX | `240` → 24.0 V. Diagnose fails below 22 V. |
| `/1?6\r` | Valve position | `/0` + status + `I`/`O`/… + ETX | `B` = bypass; later plunger moves will trip error 11. |
| `/1?\r` | Plunger position (steps) | `/0` + status + integer + ETX | 0..12000 in `N0`. |
| `/1ZR\r` | **Initialize** (CW polarity) | — | Canonical first motion command. **Not yet implemented in this driver.** |
| `/1IA6000OA0R\r` | Aspirate to 6000, then dispense to 0 | — | Multi-step example. **Not yet implemented.** |

## Install

Requires Python ≥ 3.12.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Finding the serial port

```bash
ls -l /dev/ttyUSB*
# If absent, check kernel logs for the CH340 attach:
dmesg | tail
```

`Permission denied` on the port usually means the user is missing the `dialout` group.

## First run — read-only diagnose

The safest first action: verify wiring, voltage, and identity without moving anything.

### CLI

```bash
.venv/bin/sy01b-diagnose --port /dev/ttyUSB1 --address 1 --syringe-uL 125
```

Successful output:

```
SY-01B diagnostic report
  software version : 8.33
  serial number    : 32656
  config           : ...
  supply voltage   : 24.x V
  valve position   : I
  plunger position : 0 steps
  pre-init status  : busy=False error=NOT_INITIALIZED
  ok to initialize : True
```

### Python

```python
from sy01b import SyringePumpController

cfg = SyringePumpController.Config(
    port="/dev/ttyUSB1",
    address=1,
    syringe_uL=125,
)

with SyringePumpController.open(cfg) as pump:
    report = pump.diagnose()
    print(report.render())
```

The shortest working example against real hardware is [main.py](main.py) (just `?23` + `?202`).

## When diagnose fails

| Symptom | Likely cause |
|---|---|
| `DiagnosticTimeoutError` (no reply) | Wrong port, pump unpowered, dongle LED off, cable, or **address switch ≠ `cfg.address`**. |
| `LowSupplyVoltageError` (< 22 V) | Underpowered PSU, loose DB-15. |
| `valve is in bypass` warning | Plunger moves will trip error 11 until the valve leaves bypass. |
| `pre-init status` = `NOT_INITIALIZED` | **Normal** right after power-on. Init lands in a later commit. |
| `DiagnosticGarbledReplyError` | Another serial client is on the port, or the pump locked into OEM/RUNZE this power cycle — power-cycle and let the driver send DT first. |

## What's implemented

Everything is reachable from a single import: `from sy01b import SyringePumpController`.

- DT ASCII frame builder/parser and status-byte decode (`build_command`, `parse_reply`, `StatusByte`).
- Pump error code → exception mapping under `SyringePumpController.Error` (`DeviceError`, `InitFailedError`, `PlungerOverloadError`, `CommandOverflowError`, …).
- Transport- and diagnostic-layer exceptions (`TransportError`, `ProtocolError`, `DiagnosticError`).
- `SyringePumpController.Config` — frozen dataclass with TOML loader and syringe-size → stall-current lookup.
- Read-only queries: `query_status` (`Q`), `query_software_version` (`?23`), `query_serial_number` (`?202`), `query_config` (`?76`), `query_supply_voltage_v` (`*`), `query_valve_position` (`?6`), `query_plunger_position` (`?`).
- `diagnose()` flow → `DiagnosticsReport`.
- `sy01b-diagnose` console script.

## What's not yet implemented

The motion surface is the next milestone. See [ToDo.md](ToDo.md) for the full checklist:

- `initialize(...)` — `ZR` / `YR` with force and direction options.
- `aspirate_uL` / `dispense_uL` / `move_to_steps` with volume↔step conversion.
- `valve_to` / `valve_in` / `valve_out` / `valve_bypass`.
- `abort()` and the `requires_reinit` latch.
- `_wait_until_ready` (`Q`-polling with backoff).

## Develop

```bash
.venv/bin/ruff check src tests          # lint
.venv/bin/ruff format --check src tests # format check (no rewrites)
.venv/bin/mypy                          # strict types on src/sy01b
.venv/bin/pytest                        # full suite
.venv/bin/pytest --cov=sy01b --cov-report=term-missing
```

Bench-learned lessons are collected in [LearnedPatterns.md](LearnedPatterns.md).
