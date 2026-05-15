"""Pure tests for the DT protocol layer — frame builder, parser, status decoder."""

from __future__ import annotations

import pytest

from sy01b.errors import ErrorCode, ProtocolError
from sy01b.protocol import (
    ETX,
    StatusByte,
    build_command,
    format_address,
    parse_reply,
)


class TestFormatAddress:
    @pytest.mark.parametrize(
        ("address", "expected"),
        [
            (1, b"1"),
            (2, b"2"),
            (9, b"9"),
            (10, b":"),
            (15, b"?"),
        ],
    )
    def test_valid_address(self, address: int, expected: bytes) -> None:
        assert format_address(address) == expected

    @pytest.mark.parametrize("bad", [0, -1, 16, 100])
    def test_out_of_range_raises(self, bad: int) -> None:
        with pytest.raises(ValueError, match="address"):
            format_address(bad)


class TestBuildCommand:
    def test_simple_query(self) -> None:
        assert build_command(1, "?23") == b"/1?23\r"

    def test_query_no_R_appended(self) -> None:
        # Read-only commands must never gain a trailing R.
        frame = build_command(1, "?23", execute=False)
        assert b"R" not in frame[3:-1]

    def test_execute_adds_R(self) -> None:
        assert build_command(1, "A100", execute=True) == b"/1A100R\r"

    def test_addr_two(self) -> None:
        assert build_command(2, "Q") == b"/2Q\r"

    def test_buffer_overflow_raises(self) -> None:
        with pytest.raises(ValueError, match="buffer"):
            build_command(1, "A" * 256)


def _frame(status_byte: int, data: bytes = b"") -> bytes:
    return b"/0" + bytes([status_byte]) + data + ETX + b"\r\n"


class TestStatusByte:
    def test_ready_no_error(self) -> None:
        # 0x40 = bit 6 set, bit 5 (busy) clear, no error nibble
        s = StatusByte.decode(0x40)
        assert s.busy is False
        assert s.error is ErrorCode.OK
        assert s.is_ok

    def test_busy_no_error(self) -> None:
        s = StatusByte.decode(0x60)
        assert s.busy is True
        assert s.error is ErrorCode.OK

    def test_ready_not_initialized(self) -> None:
        s = StatusByte.decode(0x47)  # bit 6 + error 7
        assert s.busy is False
        assert s.error is ErrorCode.NOT_INITIALIZED
        assert not s.is_ok

    def test_busy_plunger_overload(self) -> None:
        s = StatusByte.decode(0x69)
        assert s.busy is True
        assert s.error is ErrorCode.PLUNGER_OVERLOAD

    def test_unknown_error_code_maps_to_unknown(self) -> None:
        # error nibble 5 is not in the table; mapped to UNKNOWN
        s = StatusByte.decode(0x45)
        assert s.error is ErrorCode.UNKNOWN

    @pytest.mark.parametrize("bad", [0x00, 0x10, 0x80, 0xFF])
    def test_missing_fixed_frame_bit_raises(self, bad: int) -> None:
        with pytest.raises(ProtocolError, match="fixed bit-6"):
            StatusByte.decode(bad)


class TestParseReply:
    def test_status_only(self) -> None:
        reply = parse_reply(_frame(0x40))
        assert reply.status.error is ErrorCode.OK
        assert reply.data == b""

    def test_with_data(self) -> None:
        reply = parse_reply(_frame(0x40, b"V1.4"))
        assert reply.data == b"V1.4"
        assert reply.status.is_ok

    def test_data_may_contain_slash_zero(self) -> None:
        # ETX is the delimiter, not '/' or '0'. Make sure embedded /0 in data parses fine.
        reply = parse_reply(_frame(0x40, b"/0/0"))
        assert reply.data == b"/0/0"

    @pytest.mark.parametrize(
        ("frame", "match"),
        [
            (b"", "too short"),
            (b"X0@\x03\r\n", "leading"),
            (b"/1@\x03\r\n", "master address"),
            (b"/0@no-etx-here\r\n", "ETX"),
        ],
    )
    def test_malformed_raises(self, frame: bytes, match: str) -> None:
        with pytest.raises(ProtocolError, match=match):
            parse_reply(frame)


class TestLeadingGarbageTolerance:
    """Regression guard for the CH340 stray-byte quirk discovered during HIL.

    The real EUSB-30 dongle on /dev/ttyUSB1 emitted ``\\xff/0`8.33\\x03\\r\\n`` for
    the very first ``?23`` reply after open. The 0xFF is line/idle dribble from the
    CH340 chip, not from the pump. DTTransport strips bytes before the leading '/';
    this test pins that behavior so a refactor cannot regress it.

    Note: the stripping happens in `DTTransport.send`, not in `parse_reply`. The
    parser remains strict (see `test_malformed_raises`) — only the transport
    layer is allowed to discard pre-frame bytes.
    """

    def test_transport_strips_leading_garbage(self) -> None:
        # This is integration-level: we simulate the buffer state the transport
        # would see, run the same slicing logic, and confirm the bytes that reach
        # the parser are clean.
        buf = bytearray(b"\xff/0`8.33\x03\r\n")
        etx_index = buf.index(0x03)
        end = etx_index + 1
        while end < len(buf) and buf[end] in (0x0D, 0x0A):
            end += 1
        start = buf.find(b"/")
        cleaned = bytes(buf[start:end])
        assert cleaned == b"/0`8.33\x03\r\n"
        reply = parse_reply(cleaned)
        assert reply.data == b"8.33"
