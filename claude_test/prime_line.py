"""Prime a tube by repeatedly aspirating from port 3 and dispensing through port 1.

Bench script — runs against real hardware on /dev/ttyUSB1. Composes the existing
public API (no new driver code) into a priming workflow:

    for each cycle:
        valve → source port (default 3)      # I3R
        plunger → full stroke                 # A<stroke>R  (aspirate)
        valve → sink port   (default 1)      # I1R
        plunger → 0                           # A0R         (dispense)

Each valve move is verified via `?6`; each plunger move is verified via `?`
(per LearnedPatterns E5, Q.busy is unreliable on firmware 8.33). Runs
`diagnose()` first per LearnedPatterns W1. After the last cycle the pump is
parked at the sink port with the plunger at 0, leaving the line primed.

Volume per cycle is fixed at the full stroke (`cfg.step_mode.full_stroke_steps`)
— with a 125 µL syringe in NORMAL step mode that is 12 000 half-steps = 125 µL
moved through the line each direction per cycle. Use `--cycles` to dial total
volume; 2-3 cycles is typical to clear a short bench tube.

**Safety**: the source line (port 3) must have liquid available before running;
otherwise the aspirate stroke pulls air. The sink line (port 1) must be free
to flow. Do not run with fluid lines under pressure.

Usage:
    /opt/conda/envs/syringe/bin/python claude_test/prime_line.py
    /opt/conda/envs/syringe/bin/python claude_test/prime_line.py --cycles 5 -v
    /opt/conda/envs/syringe/bin/python claude_test/prime_line.py --source-port 3 --sink-port 1

Exit codes:
    0 — all moves verified
    1 — at least one valve or plunger move's reported position did not match
    2 — diagnose() reported the pump is not safe to drive
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from sy01b import SyringePumpController


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prime_line", description=__doc__.splitlines()[0]
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=3,
        help="Number of aspirate-dispense cycles (default 3).",
    )
    parser.add_argument(
        "--source-port",
        type=int,
        default=3,
        dest="source_port",
        help="Distribution port to aspirate from (default 3).",
    )
    parser.add_argument(
        "--sink-port",
        type=int,
        default=1,
        dest="sink_port",
        help="Distribution port to dispense through (default 1).",
    )
    parser.add_argument(
        "--force",
        type=int,
        default=2,
        choices=[0, 1, 2],
        help="Z init force code (default 2 for 125 µL bench syringe).",
    )
    parser.add_argument(
        "--settle-timeout-s",
        type=float,
        default=10.0,
        dest="settle_timeout_s",
        help="Max seconds to wait for each plunger move to settle (default 10).",
    )
    parser.add_argument(
        "--delay-s",
        type=float,
        default=0.3,
        dest="delay_s",
        help="Seconds to sleep between cycles (default 0.3).",
    )
    parser.add_argument("--port", default="/dev/ttyUSB1")
    parser.add_argument("--address", type=int, default=1)
    parser.add_argument("--baud", type=int, default=9600)
    parser.add_argument(
        "--syringe-uL", type=int, dest="syringe_uL", default=125
    )
    parser.add_argument(
        "--reply-timeout-s",
        type=float,
        dest="reply_timeout_s",
        default=2.0,
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    cfg = SyringePumpController.Config(
        port=args.port,
        address=args.address,
        baud=args.baud,
        syringe_uL=args.syringe_uL,
        step_mode=SyringePumpController.StepMode.NORMAL,
        reply_timeout_s=args.reply_timeout_s,
    )
    stroke = cfg.step_mode.full_stroke_steps
    ul_per_stroke = cfg.syringe_uL

    with SyringePumpController.open(cfg) as pump:
        report = pump.diagnose()
        print(report.render(), file=sys.stderr)
        if not report.ok_to_initialize:
            print(
                "diagnose() reports the pump is NOT safe to drive — aborting.",
                file=sys.stderr,
            )
            return 2

        print(
            f"initializing: Z{args.force}R (CW, force={args.force}) ...",
            file=sys.stderr,
        )
        t0 = time.monotonic()
        pump.initialize(force=args.force)
        init_elapsed = time.monotonic() - t0
        print(
            f"init done in {init_elapsed:.2f} s — plunger at "
            f"{pump.query_plunger_position()}, valve at "
            f"{pump.query_valve_position()!r}",
            file=sys.stderr,
        )
        print(
            f"priming {args.cycles} cycle(s): port {args.source_port} → "
            f"port {args.sink_port}, {ul_per_stroke} µL per stroke "
            f"(total ≈ {args.cycles * ul_per_stroke} µL through the line)",
            file=sys.stderr,
        )

        mismatches = 0
        # 4 verified moves per cycle: valve→src, aspirate, valve→sink, dispense.
        total = args.cycles * 4
        for cycle in range(args.cycles):
            # 1. Valve → source
            t0 = time.monotonic()
            pump.move_valve_to_port(args.source_port)
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            raw = pump.query_valve_position()
            ok = raw.strip() == str(args.source_port)
            mismatches += int(not ok)
            print(
                f"cycle {cycle:>2}  valve→{args.source_port}  "
                f"?6={raw!r:>5}  {elapsed_ms:7.1f} ms  "
                f"{'OK' if ok else 'MISMATCH'}"
            )

            # 2. Aspirate (plunger → full stroke)
            t0 = time.monotonic()
            pump.move_to_steps(stroke, settle_timeout_s=args.settle_timeout_s)
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            pos = pump.query_plunger_position()
            ok = pos == stroke
            mismatches += int(not ok)
            print(
                f"cycle {cycle:>2}  aspirate→{stroke:>5}  "
                f"?={pos:>5}  {elapsed_ms:7.1f} ms  "
                f"{'OK' if ok else 'MISMATCH'}"
            )

            # 3. Valve → sink
            t0 = time.monotonic()
            pump.move_valve_to_port(args.sink_port)
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            raw = pump.query_valve_position()
            ok = raw.strip() == str(args.sink_port)
            mismatches += int(not ok)
            print(
                f"cycle {cycle:>2}  valve→{args.sink_port}  "
                f"?6={raw!r:>5}  {elapsed_ms:7.1f} ms  "
                f"{'OK' if ok else 'MISMATCH'}"
            )

            # 4. Dispense (plunger → 0)
            t0 = time.monotonic()
            pump.move_to_steps(0, settle_timeout_s=args.settle_timeout_s)
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            pos = pump.query_plunger_position()
            ok = pos == 0
            mismatches += int(not ok)
            print(
                f"cycle {cycle:>2}  dispense→{0:>5}  "
                f"?={pos:>5}  {elapsed_ms:7.1f} ms  "
                f"{'OK' if ok else 'MISMATCH'}"
            )

            time.sleep(args.delay_s)

        end_valve = pump.query_valve_position()
        end_plunger = pump.query_plunger_position()
        print(
            f"final state: valve={end_valve!r}, plunger={end_plunger} "
            f"(expected port {args.sink_port}, plunger=0)",
            file=sys.stderr,
        )

    print(
        f"\nsummary: {total - mismatches}/{total} moves verified, "
        f"{mismatches} mismatch(es)",
        file=sys.stderr,
    )
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
