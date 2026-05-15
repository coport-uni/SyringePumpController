"""Read-only identity probe against a real SY-01B pump.

Hardware assumption: 125 uL syringe at address 1 on /dev/ttyUSB1.
Sends only ?23 (software version) and ?202 (serial number). No init,
no plunger move, no valve move.
"""

from __future__ import annotations

import logging
import sys

from sy01b import SyringePumpController


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    cfg = SyringePumpController.Config(
        port="/dev/ttyUSB1",
        address=1,
        baud=9600,
        syringe_uL=125,
        step_mode=SyringePumpController.StepMode.NORMAL,
        reply_timeout_s=2.0,
    )

    with SyringePumpController.open(cfg) as pump:
        software_version = pump.query_software_version()
        serial_number = pump.query_serial_number()

    print(f"Software version: {software_version}")
    print(f"Serial number   : {serial_number}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
