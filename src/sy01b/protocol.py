"""DT protocol primitives: frame builder, reply parser, status-byte decoding.

Pure functions and frozen dataclasses. No I/O, no global state. Mirrors the
DT framing spec in [SY01BE.pdf](../SY01BE.pdf) §4.2.2.
"""

from __future__ import annotations

from dataclasses import dataclass

from sy01b.errors import ErrorCode, ProtocolError

CR = b"\r"
LF = b"\n"
ETX = b"\x03"

STATUS_BASE_BITS = 0x40
STATUS_BUSY_BIT = 0x20
STATUS_ERROR_MASK = 0x0F

_ADDR_FIRST = ord("1")
_ADDR_LAST = ord("?")

COMMAND_BUFFER_MAX = 255


@dataclass(frozen=True, slots=True)
class StatusByte:
    busy: bool
    error: ErrorCode
    raw: int

    @classmethod
    def decode(cls, byte: int) -> StatusByte:
        if (byte & 0x80) != 0 or (byte & STATUS_BASE_BITS) != STATUS_BASE_BITS:
            raise ProtocolError(f"status byte {byte:#04x} missing fixed bit-6 frame")
        busy = (byte & STATUS_BUSY_BIT) != 0
        error = ErrorCode.from_byte(byte & STATUS_ERROR_MASK)
        return cls(busy=busy, error=error, raw=byte)

    @property
    def is_ok(self) -> bool:
        return self.error is ErrorCode.OK


@dataclass(frozen=True, slots=True)
class Reply:
    status: StatusByte
    data: bytes


def format_address(address: int) -> bytes:
    if not 1 <= address <= 15:
        raise ValueError(f"address must be in 1..15, got {address}")
    return bytes([_ADDR_FIRST + address - 1])


def build_command(address: int, cmds: str, *, execute: bool = False) -> bytes:
    body = cmds.encode("ascii")
    if execute:
        body = body + b"R"
    if len(body) > COMMAND_BUFFER_MAX:
        raise ValueError(
            f"command body is {len(body)} bytes, exceeds {COMMAND_BUFFER_MAX}-byte pump buffer"
        )
    return b"/" + format_address(address) + body + CR


def parse_reply(frame: bytes) -> Reply:
    if len(frame) < 5:
        raise ProtocolError(f"reply too short ({len(frame)} bytes): {frame!r}", raw=frame)
    if frame[0:1] != b"/":
        raise ProtocolError(f"reply missing leading '/': {frame!r}", raw=frame)
    if frame[1:2] != b"0":
        raise ProtocolError(
            f"reply master address is {frame[1:2]!r}, expected b'0'",
            raw=frame,
        )
    etx_index = frame.find(ETX, 3)
    if etx_index < 0:
        raise ProtocolError(f"reply missing ETX terminator: {frame!r}", raw=frame)
    status = StatusByte.decode(frame[2])
    data = bytes(frame[3:etx_index])
    return Reply(status=status, data=data)


# Read-only command strings. Kept as constants (not functions) because they take no parameters
# and the manual's command set is fixed; functions would add noise without adding flexibility.

CMD_QUERY_STATUS = "Q"
CMD_QUERY_SOFTWARE_VERSION = "?23"
CMD_QUERY_SERIAL_NUMBER = "?202"
CMD_QUERY_CONFIG = "?76"
CMD_QUERY_SUPPLY_VOLTAGE = "*"
CMD_QUERY_VALVE_POSITION = "?6"
CMD_QUERY_PLUNGER_POSITION = "?"
