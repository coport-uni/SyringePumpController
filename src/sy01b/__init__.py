"""Python controller for the Runze SY-01B Smart Syringe Pump (DT ASCII protocol)."""

from __future__ import annotations

from sy01b.config import ALLOWED_SYRINGES_UL, PumpConfig, StepMode
from sy01b.diagnostics import MIN_SUPPLY_VOLTS, DiagnosticsReport, diagnose
from sy01b.errors import (
    CommandOverflowError,
    DeviceError,
    DiagnosticError,
    DiagnosticGarbledReplyError,
    DiagnosticTimeoutError,
    ErrorCode,
    InitFailedError,
    InvalidCommandError,
    InvalidOperandError,
    LowSupplyVoltageError,
    NotInitializedError,
    PlungerBlockedByBypassError,
    PlungerOverloadError,
    ProtocolError,
    PumpError,
    TransportClosed,
    TransportError,
    TransportTimeout,
    ValveOverloadError,
    WrongAddressError,
)
from sy01b.protocol import Reply, StatusByte
from sy01b.pump import Pump
from sy01b.transport import DTTransport, Transport

__version__ = "0.1.0.dev0"

__all__ = [
    "ALLOWED_SYRINGES_UL",
    "MIN_SUPPLY_VOLTS",
    "CommandOverflowError",
    "DTTransport",
    "DeviceError",
    "DiagnosticError",
    "DiagnosticGarbledReplyError",
    "DiagnosticTimeoutError",
    "DiagnosticsReport",
    "ErrorCode",
    "InitFailedError",
    "InvalidCommandError",
    "InvalidOperandError",
    "LowSupplyVoltageError",
    "NotInitializedError",
    "PlungerBlockedByBypassError",
    "PlungerOverloadError",
    "ProtocolError",
    "Pump",
    "PumpConfig",
    "PumpError",
    "Reply",
    "StatusByte",
    "StepMode",
    "Transport",
    "TransportClosed",
    "TransportError",
    "TransportTimeout",
    "ValveOverloadError",
    "WrongAddressError",
    "__version__",
    "diagnose",
]
