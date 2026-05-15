"""Exception hierarchy and ErrorCode enum for the SY-01B controller.

Layered so that no other sy01b module needs to import errors only to raise;
foundational and import-free.
"""

from __future__ import annotations

from enum import IntEnum


class ErrorCode(IntEnum):
    OK = 0
    INIT_FAILED = 1
    INVALID_COMMAND = 2
    INVALID_OPERAND = 3
    NOT_INITIALIZED = 7
    PLUNGER_OVERLOAD = 9
    VALVE_OVERLOAD = 10
    PLUNGER_BLOCKED_BY_BYPASS = 11
    COMMAND_OVERFLOW = 15
    UNKNOWN = 0xFF

    @classmethod
    def from_byte(cls, nibble: int) -> ErrorCode:
        try:
            return cls(nibble)
        except ValueError:
            return cls.UNKNOWN


class PumpError(Exception):
    """Base for every error raised by the sy01b package."""


class TransportError(PumpError):
    """Anything wrong at the serial / framing layer."""


class TransportTimeout(TransportError):
    def __init__(self, elapsed_s: float, frame_sent: bytes, partial: bytes) -> None:
        super().__init__(f"no ETX within {elapsed_s:.3f}s; sent={frame_sent!r} partial={partial!r}")
        self.elapsed_s = elapsed_s
        self.frame_sent = frame_sent
        self.partial = partial


class TransportClosed(TransportError):
    """Operation attempted on a closed transport."""


class ProtocolError(PumpError):
    """Reply bytes received but the frame is malformed."""

    def __init__(self, message: str, raw: bytes = b"") -> None:
        super().__init__(message)
        self.raw = raw


class DeviceError(PumpError):
    """Pump returned a non-OK error code in its status byte."""

    def __init__(
        self,
        error_code: ErrorCode,
        command_sent: str,
        raw_reply: bytes,
    ) -> None:
        super().__init__(
            f"{type(self).__name__}: code={int(error_code)} "
            f"cmd={command_sent!r} reply={raw_reply.hex()}"
        )
        self.error_code = error_code
        self.command_sent = command_sent
        self.raw_reply = raw_reply


class InitFailedError(DeviceError):
    """Error 1 — initialization failed. Pump rejects further commands until cleared."""


class InvalidCommandError(DeviceError):
    """Error 2."""


class InvalidOperandError(DeviceError):
    """Error 3."""


class NotInitializedError(DeviceError):
    """Error 7 — device has not been initialized yet."""


class PlungerOverloadError(DeviceError):
    """Error 9 — plunger overload; must re-initialize."""


class ValveOverloadError(DeviceError):
    """Error 10."""


class PlungerBlockedByBypassError(DeviceError):
    """Error 11."""


class CommandOverflowError(DeviceError):
    """Error 15 — move sent while still moving."""


_DEVICE_ERROR_BY_CODE: dict[ErrorCode, type[DeviceError]] = {
    ErrorCode.INIT_FAILED: InitFailedError,
    ErrorCode.INVALID_COMMAND: InvalidCommandError,
    ErrorCode.INVALID_OPERAND: InvalidOperandError,
    ErrorCode.NOT_INITIALIZED: NotInitializedError,
    ErrorCode.PLUNGER_OVERLOAD: PlungerOverloadError,
    ErrorCode.VALVE_OVERLOAD: ValveOverloadError,
    ErrorCode.PLUNGER_BLOCKED_BY_BYPASS: PlungerBlockedByBypassError,
    ErrorCode.COMMAND_OVERFLOW: CommandOverflowError,
}


def device_error_for(code: ErrorCode) -> type[DeviceError]:
    return _DEVICE_ERROR_BY_CODE.get(code, DeviceError)


class DiagnosticError(PumpError):
    """Base for failures of the read-only diagnostic stage."""


class DiagnosticTimeoutError(DiagnosticError):
    """A diagnostic probe timed out."""


class DiagnosticGarbledReplyError(DiagnosticError):
    """A diagnostic probe got a reply that did not parse as DT."""


class LowSupplyVoltageError(DiagnosticError):
    def __init__(self, measured_v: float, min_v: float) -> None:
        super().__init__(f"supply voltage {measured_v:.1f}V below floor {min_v:.1f}V")
        self.measured_v = measured_v
        self.min_v = min_v


class WrongAddressError(DiagnosticError):
    """Reply parsed but the echoed address byte did not match the configured one."""
