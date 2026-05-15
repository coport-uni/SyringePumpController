"""Coverage for the exception module — error-code mapping and message formatting."""

from __future__ import annotations

import pytest

from sy01b.errors import (
    CommandOverflowError,
    DeviceError,
    ErrorCode,
    InitFailedError,
    InvalidCommandError,
    InvalidOperandError,
    NotInitializedError,
    PlungerBlockedByBypassError,
    PlungerOverloadError,
    TransportTimeout,
    ValveOverloadError,
    device_error_for,
)


class TestDeviceErrorMapping:
    @pytest.mark.parametrize(
        ("code", "cls"),
        [
            (ErrorCode.INIT_FAILED, InitFailedError),
            (ErrorCode.INVALID_COMMAND, InvalidCommandError),
            (ErrorCode.INVALID_OPERAND, InvalidOperandError),
            (ErrorCode.NOT_INITIALIZED, NotInitializedError),
            (ErrorCode.PLUNGER_OVERLOAD, PlungerOverloadError),
            (ErrorCode.VALVE_OVERLOAD, ValveOverloadError),
            (ErrorCode.PLUNGER_BLOCKED_BY_BYPASS, PlungerBlockedByBypassError),
            (ErrorCode.COMMAND_OVERFLOW, CommandOverflowError),
        ],
    )
    def test_known_codes_map_to_specific_class(
        self, code: ErrorCode, cls: type[DeviceError]
    ) -> None:
        assert device_error_for(code) is cls

    def test_unknown_code_falls_back_to_base(self) -> None:
        assert device_error_for(ErrorCode.UNKNOWN) is DeviceError


class TestDeviceErrorMessage:
    def test_includes_code_and_command(self) -> None:
        exc = InitFailedError(
            error_code=ErrorCode.INIT_FAILED, command_sent="ZR", raw_reply=b"/0A\x03"
        )
        msg = str(exc)
        assert "InitFailedError" in msg
        assert "code=1" in msg
        assert "ZR" in msg

    def test_carries_diagnostic_fields(self) -> None:
        exc = PlungerOverloadError(
            error_code=ErrorCode.PLUNGER_OVERLOAD, command_sent="A6000R", raw_reply=b"/0I\x03"
        )
        assert exc.error_code is ErrorCode.PLUNGER_OVERLOAD
        assert exc.command_sent == "A6000R"
        assert exc.raw_reply == b"/0I\x03"


class TestTransportTimeoutMessage:
    def test_carries_elapsed_and_partial(self) -> None:
        exc = TransportTimeout(elapsed_s=0.5, frame_sent=b"/1Q\r", partial=b"abc")
        msg = str(exc)
        assert "0.500" in msg
        assert "/1Q" in msg
        assert "abc" in msg
        assert exc.elapsed_s == 0.5
        assert exc.partial == b"abc"


class TestErrorCodeFromByte:
    def test_known_nibble(self) -> None:
        assert ErrorCode.from_byte(7) is ErrorCode.NOT_INITIALIZED

    def test_unknown_nibble_maps_to_unknown(self) -> None:
        assert ErrorCode.from_byte(5) is ErrorCode.UNKNOWN
        assert ErrorCode.from_byte(6) is ErrorCode.UNKNOWN
        assert ErrorCode.from_byte(8) is ErrorCode.UNKNOWN
