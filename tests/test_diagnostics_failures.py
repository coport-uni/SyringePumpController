"""Negative-path tests for the diagnostic stage."""

from __future__ import annotations

import pytest

from sy01b.diagnostics import diagnose
from sy01b.errors import (
    DiagnosticGarbledReplyError,
    DiagnosticTimeoutError,
    LowSupplyVoltageError,
    TransportTimeout,
)
from sy01b.pump import Pump
from tests.conftest import FakeTransport, dt_reply


class _TimingOutTransport:
    """Transport stub that always raises TransportTimeout, regardless of the frame sent."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def send(self, frame: bytes, deadline_s: float) -> bytes:
        self.sent.append(frame)
        raise TransportTimeout(elapsed_s=deadline_s, frame_sent=frame, partial=b"")

    def close(self) -> None:
        pass


class _GarbledTransport:
    """Transport stub that returns frames without an ETX terminator."""

    def send(self, frame: bytes, deadline_s: float) -> bytes:
        return b"/0@no-etx"

    def close(self) -> None:
        pass


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def pump(transport: FakeTransport) -> Pump:
    return Pump(transport=transport, address=1, reply_timeout_s=1.0)


def _scripted(transport: FakeTransport, *, voltage_bytes: bytes = b"240") -> None:
    transport.expect(b"/1Q\r", dt_reply(0x40))
    transport.expect(b"/1?23\r", dt_reply(0x40, b"V1.4"))
    transport.expect(b"/1?202\r", dt_reply(0x40, b"SN-1"))
    transport.expect(b"/1?76\r", dt_reply(0x40, b"cfg"))
    transport.expect(b"/1*\r", dt_reply(0x40, voltage_bytes))
    transport.expect(b"/1?6\r", dt_reply(0x40, b"I"))
    transport.expect(b"/1?\r", dt_reply(0x40, b"0"))


class TestHardFails:
    def test_timeout_on_first_probe_raises_diagnostic_timeout(self) -> None:
        timing_out = _TimingOutTransport()
        pump = Pump(transport=timing_out, address=1, reply_timeout_s=0.01)
        with pytest.raises(DiagnosticTimeoutError):
            diagnose(pump)

    def test_garbled_reply_raises_diagnostic_garbled(self) -> None:
        garbled = _GarbledTransport()
        pump = Pump(transport=garbled, address=1, reply_timeout_s=1.0)
        with pytest.raises(DiagnosticGarbledReplyError):
            diagnose(pump)

    def test_low_supply_voltage_raises(self, transport: FakeTransport, pump: Pump) -> None:
        # 21.9 V < 22.0 V floor
        _scripted(transport, voltage_bytes=b"219")
        with pytest.raises(LowSupplyVoltageError) as ei:
            diagnose(pump)
        assert ei.value.measured_v == pytest.approx(21.9)


class TestWarnings:
    def test_valve_in_bypass_emits_warning(self, transport: FakeTransport, pump: Pump) -> None:
        transport.expect(b"/1Q\r", dt_reply(0x40))
        transport.expect(b"/1?23\r", dt_reply(0x40, b"V1.4"))
        transport.expect(b"/1?202\r", dt_reply(0x40, b"SN-1"))
        transport.expect(b"/1?76\r", dt_reply(0x40, b"cfg"))
        transport.expect(b"/1*\r", dt_reply(0x40, b"240"))
        transport.expect(b"/1?6\r", dt_reply(0x40, b"B"))  # bypass
        transport.expect(b"/1?\r", dt_reply(0x40, b"0"))
        report = diagnose(pump)
        assert any("bypass" in w for w in report.warnings)


class TestRendering:
    def test_render_contains_identity_fields(self, transport: FakeTransport, pump: Pump) -> None:
        _scripted(transport)
        report = diagnose(pump)
        rendered = report.render()
        assert "V1.4" in rendered
        assert "SN-1" in rendered
        assert "24.0 V" in rendered
