"""Pump-level tests driven by a scripted FakeTransport.

Verifies that the high-level read-only methods produce the exact frames
specified by the manual and correctly decode the replies.
"""

from __future__ import annotations

import pytest

from sy01b.errors import ErrorCode, ProtocolError, TransportClosed
from sy01b.pump import Pump
from tests.conftest import FakeTransport, dt_reply


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def pump(transport: FakeTransport) -> Pump:
    return Pump(transport=transport, address=1, reply_timeout_s=1.0)


class TestReadOnlyQueries:
    def test_query_status_ready_ok(self, transport: FakeTransport, pump: Pump) -> None:
        transport.expect(b"/1Q\r", dt_reply(0x40))
        status = pump.query_status()
        assert status.busy is False
        assert status.error is ErrorCode.OK

    def test_query_status_returns_not_initialized_without_raising(
        self, transport: FakeTransport, pump: Pump
    ) -> None:
        # Q is special: its status byte IS the data. Even error-7 must be returned, not raised,
        # because callers need to read pre-init state.
        transport.expect(b"/1Q\r", dt_reply(0x47))
        status = pump.query_status()
        assert status.error is ErrorCode.NOT_INITIALIZED

    def test_query_software_version_returns_data(
        self, transport: FakeTransport, pump: Pump
    ) -> None:
        transport.expect(b"/1?23\r", dt_reply(0x40, b"V1.4"))
        assert pump.query_software_version() == "V1.4"

    def test_query_serial_number_returns_data(self, transport: FakeTransport, pump: Pump) -> None:
        transport.expect(b"/1?202\r", dt_reply(0x40, b"RZ-SY01B-2024-12345"))
        assert pump.query_serial_number() == "RZ-SY01B-2024-12345"

    def test_query_supply_voltage_converts_tenths(
        self, transport: FakeTransport, pump: Pump
    ) -> None:
        # Manual §4.5 (* command): pump reports volts x 10. 240 -> 24.0 V.
        transport.expect(b"/1*\r", dt_reply(0x40, b"240"))
        assert pump.query_supply_voltage_v() == pytest.approx(24.0)

    def test_query_supply_voltage_rejects_non_numeric(
        self, transport: FakeTransport, pump: Pump
    ) -> None:
        transport.expect(b"/1*\r", dt_reply(0x40, b"BAD"))
        with pytest.raises(ProtocolError, match="not a number"):
            pump.query_supply_voltage_v()

    def test_query_plunger_position_parses_int(self, transport: FakeTransport, pump: Pump) -> None:
        transport.expect(b"/1?\r", dt_reply(0x40, b"6000"))
        assert pump.query_plunger_position() == 6000

    def test_query_valve_position_returns_string(
        self, transport: FakeTransport, pump: Pump
    ) -> None:
        transport.expect(b"/1?6\r", dt_reply(0x40, b"I"))
        assert pump.query_valve_position() == "I"


class TestAddressing:
    def test_alternate_address_in_frame(self) -> None:
        transport = FakeTransport()
        pump = Pump(transport=transport, address=3, reply_timeout_s=1.0)
        transport.expect(b"/3Q\r", dt_reply(0x40))
        pump.query_status()
        # Pump-side address is encoded in byte 2 of the *outgoing* frame, not in the reply.
        assert transport.sent == [b"/3Q\r"]


class TestContextManager:
    def test_close_on_exit(self, transport: FakeTransport, pump: Pump) -> None:
        with pump:
            transport.expect(b"/1Q\r", dt_reply(0x40))
            pump.query_status()
        assert transport.closed

    def test_closed_transport_raises(self, transport: FakeTransport, pump: Pump) -> None:
        transport.close()
        with pytest.raises(TransportClosed):
            pump.query_status()


class TestNoMotionCommandsExposed:
    """Defensive: this commit must not ship motion methods. Catch accidental scope creep."""

    def test_pump_has_no_initialize(self, pump: Pump) -> None:
        assert not hasattr(pump, "initialize")

    def test_pump_has_no_aspirate(self, pump: Pump) -> None:
        assert not hasattr(pump, "aspirate_uL")

    def test_pump_has_no_abort(self, pump: Pump) -> None:
        assert not hasattr(pump, "abort")
