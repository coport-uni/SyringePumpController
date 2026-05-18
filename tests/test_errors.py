"""Coverage for the exception module — error-code mapping and message formatting."""

from __future__ import annotations

import pytest

from sy01b import SyringePumpController


class TestDeviceErrorMapping:
    @pytest.mark.parametrize(
        ("code", "cls"),
        [
            (
                SyringePumpController.ErrorCode.INIT_FAILED,
                SyringePumpController.InitFailedError,
            ),
            (
                SyringePumpController.ErrorCode.INVALID_COMMAND,
                SyringePumpController.InvalidCommandError,
            ),
            (
                SyringePumpController.ErrorCode.INVALID_OPERAND,
                SyringePumpController.InvalidOperandError,
            ),
            (
                SyringePumpController.ErrorCode.NOT_INITIALIZED,
                SyringePumpController.NotInitializedError,
            ),
            (
                SyringePumpController.ErrorCode.PLUNGER_OVERLOAD,
                SyringePumpController.PlungerOverloadError,
            ),
            (
                SyringePumpController.ErrorCode.VALVE_OVERLOAD,
                SyringePumpController.ValveOverloadError,
            ),
            (
                SyringePumpController.ErrorCode.PLUNGER_BLOCKED_BY_BYPASS,
                SyringePumpController.PlungerBlockedByBypassError,
            ),
            (
                SyringePumpController.ErrorCode.COMMAND_OVERFLOW,
                SyringePumpController.CommandOverflowError,
            ),
        ],
    )
    def test_known_codes_map_to_specific_class(
        self,
        code: SyringePumpController.ErrorCode,
        cls: type[SyringePumpController.DeviceError],
    ) -> None:
        assert SyringePumpController.device_error_for(code) is cls

    def test_unknown_code_falls_back_to_base(self) -> None:
        assert (
            SyringePumpController.device_error_for(
                SyringePumpController.ErrorCode.UNKNOWN
            )
            is SyringePumpController.DeviceError
        )


class TestDeviceErrorMessage:
    def test_includes_code_and_command(self) -> None:
        exc = SyringePumpController.InitFailedError(
            error_code=SyringePumpController.ErrorCode.INIT_FAILED,
            command_sent="ZR",
            raw_reply=b"/0A\x03",
        )
        msg = str(exc)
        assert "InitFailedError" in msg
        assert "code=1" in msg
        assert "ZR" in msg

    def test_carries_diagnostic_fields(self) -> None:
        exc = SyringePumpController.PlungerOverloadError(
            error_code=SyringePumpController.ErrorCode.PLUNGER_OVERLOAD,
            command_sent="A6000R",
            raw_reply=b"/0I\x03",
        )
        assert (
            exc.error_code is SyringePumpController.ErrorCode.PLUNGER_OVERLOAD
        )
        assert exc.command_sent == "A6000R"
        assert exc.raw_reply == b"/0I\x03"


class TestTransportTimeoutMessage:
    def test_carries_elapsed_and_partial(self) -> None:
        exc = SyringePumpController.TransportTimeout(
            elapsed_s=0.5, frame_sent=b"/1Q\r", partial=b"abc"
        )
        msg = str(exc)
        assert "0.500" in msg
        assert "/1Q" in msg
        assert "abc" in msg
        assert exc.elapsed_s == 0.5
        assert exc.partial == b"abc"


class TestErrorCodeFromByte:
    def test_known_nibble(self) -> None:
        assert (
            SyringePumpController.ErrorCode.from_byte(7)
            is SyringePumpController.ErrorCode.NOT_INITIALIZED
        )

    def test_unknown_nibble_maps_to_unknown(self) -> None:
        assert (
            SyringePumpController.ErrorCode.from_byte(5)
            is SyringePumpController.ErrorCode.UNKNOWN
        )
        assert (
            SyringePumpController.ErrorCode.from_byte(6)
            is SyringePumpController.ErrorCode.UNKNOWN
        )
        assert (
            SyringePumpController.ErrorCode.from_byte(8)
            is SyringePumpController.ErrorCode.UNKNOWN
        )
