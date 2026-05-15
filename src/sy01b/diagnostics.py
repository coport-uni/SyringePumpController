"""Read-only diagnostic stage. See DESIGN.md §7."""

from __future__ import annotations

from dataclasses import dataclass, field

from sy01b._logging import logger
from sy01b.errors import (
    DiagnosticGarbledReplyError,
    DiagnosticTimeoutError,
    ErrorCode,
    LowSupplyVoltageError,
    ProtocolError,
    TransportTimeout,
)
from sy01b.protocol import StatusByte
from sy01b.pump import Pump

MIN_SUPPLY_VOLTS = 22.0


@dataclass(frozen=True, slots=True)
class DiagnosticsReport:
    software_version: str
    serial_number: str
    config: str
    supply_volts: float
    valve_position: str
    plunger_steps: int
    pre_init_status: StatusByte
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok_to_initialize(self) -> bool:
        return self.pre_init_status.error in {ErrorCode.OK, ErrorCode.NOT_INITIALIZED}

    def render(self) -> str:
        lines = [
            "SY-01B diagnostic report",
            f"  software version : {self.software_version}",
            f"  serial number    : {self.serial_number}",
            f"  config           : {self.config}",
            f"  supply voltage   : {self.supply_volts:.1f} V",
            f"  valve position   : {self.valve_position}",
            f"  plunger position : {self.plunger_steps} steps",
            f"  pre-init status  : busy={self.pre_init_status.busy} "
            f"error={self.pre_init_status.error.name}",
            f"  ok to initialize : {self.ok_to_initialize}",
        ]
        if self.warnings:
            lines.append("  warnings:")
            lines.extend(f"    - {w}" for w in self.warnings)
        return "\n".join(lines)


def diagnose(pump: Pump) -> DiagnosticsReport:
    """Run the read-only commissioning probe.

    Sends no `R`, no `Z`/`Y`/`W`, never moves the plunger or the valve.
    Raises a `DiagnosticError` subclass on hard fail; the caller decides
    whether to proceed to `initialize()`.
    """
    logger.info("starting diagnostic probe (read-only)")

    try:
        status = pump.query_status()
    except TransportTimeout as exc:
        raise DiagnosticTimeoutError(f"echo probe Q timed out: {exc}") from exc
    except ProtocolError as exc:
        raise DiagnosticGarbledReplyError(f"echo probe Q reply malformed: {exc}") from exc

    if status.error not in {ErrorCode.OK, ErrorCode.NOT_INITIALIZED}:
        # Treat any other code (init-failed, plunger-overload, etc.) as a hard fail —
        # the operator needs to clear it before commissioning continues.
        logger.warning("pre-init status reports error %s", status.error.name)

    try:
        software_version = pump.query_software_version()
        serial_number = pump.query_serial_number()
        config = pump.query_config()
        supply_volts = pump.query_supply_voltage_v()
        valve_position = pump.query_valve_position()
        plunger_steps = pump.query_plunger_position()
    except TransportTimeout as exc:
        raise DiagnosticTimeoutError(f"probe timed out: {exc}") from exc
    except ProtocolError as exc:
        raise DiagnosticGarbledReplyError(f"probe reply malformed: {exc}") from exc

    if supply_volts < MIN_SUPPLY_VOLTS:
        raise LowSupplyVoltageError(measured_v=supply_volts, min_v=MIN_SUPPLY_VOLTS)

    warnings: list[str] = []
    if valve_position.upper() == "B":
        warnings.append("valve is in bypass — plunger moves will fail with error 11")

    report = DiagnosticsReport(
        software_version=software_version,
        serial_number=serial_number,
        config=config,
        supply_volts=supply_volts,
        valve_position=valve_position,
        plunger_steps=plunger_steps,
        pre_init_status=status,
        warnings=tuple(warnings),
    )
    logger.info("diagnostic probe complete: %s", report.render().splitlines()[0])
    return report
