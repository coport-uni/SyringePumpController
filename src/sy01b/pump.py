"""High-level Pump class — read-only API surface.

Motion methods (initialize, aspirate, dispense, abort) live behind their
own milestone and are not implemented in this commit. The scope here is
the verification path: open the port, prove communication, read identity
fields (software version, serial number), read supply voltage. See
[DESIGN.md](../../DESIGN.md) §6, §7, §10.1.
"""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sy01b.config import PumpConfig
from sy01b.errors import ProtocolError, WrongAddressError
from sy01b.protocol import (
    CMD_QUERY_CONFIG,
    CMD_QUERY_PLUNGER_POSITION,
    CMD_QUERY_SERIAL_NUMBER,
    CMD_QUERY_SOFTWARE_VERSION,
    CMD_QUERY_STATUS,
    CMD_QUERY_SUPPLY_VOLTAGE,
    CMD_QUERY_VALVE_POSITION,
    Reply,
    StatusByte,
    build_command,
    parse_reply,
)
from sy01b.transport import DTTransport, Transport


class Pump:
    """Driver for a single SY-01B at a single address on a single transport."""

    def __init__(self, transport: Transport, address: int, reply_timeout_s: float) -> None:
        self._transport = transport
        self._address = address
        self._reply_timeout_s = reply_timeout_s

    @classmethod
    def open(cls, cfg: PumpConfig) -> Pump:
        transport = DTTransport.open(cfg)
        return cls(transport=transport, address=cfg.address, reply_timeout_s=cfg.reply_timeout_s)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._transport.close()

    def _query(self, cmds: str) -> Reply:
        frame = build_command(self._address, cmds, execute=False)
        raw = self._transport.send(frame, deadline_s=self._reply_timeout_s)
        return parse_reply(raw)

    def query_status(self) -> StatusByte:
        return self._query(CMD_QUERY_STATUS).status

    def query_software_version(self) -> str:
        return self._decode_ascii(self._query(CMD_QUERY_SOFTWARE_VERSION).data, "software version")

    def query_serial_number(self) -> str:
        return self._decode_ascii(self._query(CMD_QUERY_SERIAL_NUMBER).data, "serial number")

    def query_config(self) -> str:
        return self._decode_ascii(self._query(CMD_QUERY_CONFIG).data, "config")

    def query_supply_voltage_v(self) -> float:
        text = self._decode_ascii(self._query(CMD_QUERY_SUPPLY_VOLTAGE).data, "supply voltage")
        try:
            tenths_of_volts = int(text)
        except ValueError as exc:
            raise ProtocolError(f"supply voltage reply is not a number: {text!r}") from exc
        return tenths_of_volts / 10.0

    def query_valve_position(self) -> str:
        return self._decode_ascii(self._query(CMD_QUERY_VALVE_POSITION).data, "valve position")

    def query_plunger_position(self) -> int:
        text = self._decode_ascii(self._query(CMD_QUERY_PLUNGER_POSITION).data, "plunger position")
        try:
            return int(text)
        except ValueError as exc:
            raise ProtocolError(f"plunger position reply is not a number: {text!r}") from exc

    def assert_address_echo(self, frame_sent: bytes, reply_seen: bytes) -> None:
        """Sanity check used by the diagnostic stage.

        DT replies always echo b'0' as the master address (per spec), not the
        pump's own address. So we can only check the leading bytes match the
        DT convention — that the *pump* is using DT framing at all. The
        proof that the right pump answered is implicit: we sent to address N
        and got a syntactically valid DT reply. Mismatch would surface as a
        timeout, not a parse error.
        """
        if reply_seen[:2] != b"/0":
            raise WrongAddressError(
                f"reply header {reply_seen[:2]!r} is not DT-shaped (sent {frame_sent!r})"
            )

    @property
    def address(self) -> int:
        return self._address

    def _decode_ascii(self, data: bytes, field: str) -> str:
        try:
            return data.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise ProtocolError(f"{field} reply is not ASCII: {data!r}") from exc
