"""Identity verification test — the user-facing acceptance check.

Goal (set 2026-05-15): prove that the read-only path correctly retrieves
the product's *software version* and *serial number* from the pump,
without sending any motion command. This test is the closest thing we
have to a hardware-in-the-loop check without actually putting a pump on
the bench: a `FakeTransport` plays the role of a real pump that replies
exactly per [SY01BE.pdf](../SY01BE.pdf) §4.2.2 (DT framing) and §B.6
(report-command list).

If this test passes, a tech in the lab plugging in a real pump and
running `sy01b-diagnose` should see the same values come back, modulo
real-line jitter.
"""

from __future__ import annotations

import pytest

from sy01b import ErrorCode, PumpConfig, StepMode, diagnose
from sy01b.diagnostics import DiagnosticsReport
from sy01b.pump import Pump
from tests.conftest import FakeTransport, dt_reply

# A realistic identity payload that a real pump might report. Values are chosen to be
# distinct enough that any parser bug would produce a noticeably wrong string.
EXPECTED_SOFTWARE_VERSION = "V1.4"
EXPECTED_SERIAL_NUMBER = "RZ-SY01B-2025-04-16-00001"
EXPECTED_CONFIG_BLOB = "syringe=5000uL,steps=N0,addr=1,baud=9600"
EXPECTED_SUPPLY_VOLTS = 24.0
EXPECTED_VALVE_POSITION = "I"
EXPECTED_PLUNGER_STEPS = 0


def _scripted_pump_replies(transport: FakeTransport, *, pre_init: bool) -> None:
    """Populate the FakeTransport with replies a real pump would emit pre- or post-init."""
    status = 0x47 if pre_init else 0x40  # error 7 = NotInitialized, else OK

    # The diagnostic flow probes in this exact order — see diagnostics.diagnose().
    transport.expect(b"/1Q\r", dt_reply(status))
    transport.expect(b"/1?23\r", dt_reply(status, EXPECTED_SOFTWARE_VERSION.encode("ascii")))
    transport.expect(b"/1?202\r", dt_reply(status, EXPECTED_SERIAL_NUMBER.encode("ascii")))
    transport.expect(b"/1?76\r", dt_reply(status, EXPECTED_CONFIG_BLOB.encode("ascii")))
    transport.expect(b"/1*\r", dt_reply(status, b"240"))  # 240 deci-volts → 24.0 V
    transport.expect(b"/1?6\r", dt_reply(status, EXPECTED_VALVE_POSITION.encode("ascii")))
    transport.expect(b"/1?\r", dt_reply(status, str(EXPECTED_PLUNGER_STEPS).encode("ascii")))


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def pump(transport: FakeTransport) -> Pump:
    return Pump(transport=transport, address=1, reply_timeout_s=1.0)


class TestIndividualIdentityProbes:
    """Each read-only probe must round-trip exactly the bytes the manual specifies."""

    def test_software_version_round_trips(self, transport: FakeTransport, pump: Pump) -> None:
        transport.expect(b"/1?23\r", dt_reply(0x40, EXPECTED_SOFTWARE_VERSION.encode("ascii")))
        assert pump.query_software_version() == EXPECTED_SOFTWARE_VERSION
        assert transport.sent == [b"/1?23\r"], "must use ?23 with no trailing R"

    def test_serial_number_round_trips(self, transport: FakeTransport, pump: Pump) -> None:
        transport.expect(b"/1?202\r", dt_reply(0x40, EXPECTED_SERIAL_NUMBER.encode("ascii")))
        assert pump.query_serial_number() == EXPECTED_SERIAL_NUMBER
        assert transport.sent == [b"/1?202\r"], "must use ?202 with no trailing R"


class TestFullDiagnosticFlow:
    """The whole read-only probe assembled into a DiagnosticsReport."""

    def test_pre_init_pump_produces_complete_report(
        self, transport: FakeTransport, pump: Pump
    ) -> None:
        _scripted_pump_replies(transport, pre_init=True)
        report = diagnose(pump)

        assert isinstance(report, DiagnosticsReport)
        assert report.software_version == EXPECTED_SOFTWARE_VERSION
        assert report.serial_number == EXPECTED_SERIAL_NUMBER
        assert report.config == EXPECTED_CONFIG_BLOB
        assert report.supply_volts == pytest.approx(EXPECTED_SUPPLY_VOLTS)
        assert report.valve_position == EXPECTED_VALVE_POSITION
        assert report.plunger_steps == EXPECTED_PLUNGER_STEPS
        assert report.pre_init_status.error is ErrorCode.NOT_INITIALIZED
        assert report.ok_to_initialize is True
        assert transport.all_consumed()

    def test_already_initialized_pump_also_produces_complete_report(
        self, transport: FakeTransport, pump: Pump
    ) -> None:
        _scripted_pump_replies(transport, pre_init=False)
        report = diagnose(pump)

        assert report.software_version == EXPECTED_SOFTWARE_VERSION
        assert report.serial_number == EXPECTED_SERIAL_NUMBER
        assert report.pre_init_status.error is ErrorCode.OK
        assert report.ok_to_initialize is True

    def test_diagnose_never_sends_R_or_init_command(
        self, transport: FakeTransport, pump: Pump
    ) -> None:
        """The acceptance criterion that gives this commit its name: the path under test
        must NOT send any motion-capable command."""
        _scripted_pump_replies(transport, pre_init=True)
        diagnose(pump)

        for frame in transport.sent:
            assert not frame.endswith(b"R\r"), (
                f"frame {frame!r} ends with 'R\\r' — that would execute a queued command"
            )
            # Strip the leading '/<addr>' and trailing '\r'; the rest is the command body.
            body = frame[2:-1]
            assert body[:1] not in (b"Z", b"Y", b"W"), (
                f"frame {frame!r} starts with an init command — diagnose() must be read-only"
            )


class TestConfigDrivenAddress:
    """The verification must also pass on a pump at an address other than 1, because
    operators wire multi-pump benches and the diagnostic CLI accepts any 1..15."""

    def test_address_three(self) -> None:
        transport = FakeTransport()
        cfg = PumpConfig(
            port="loop://",  # not actually opened; we instantiate Pump directly
            address=3,
            syringe_uL=1000,
            step_mode=StepMode.NORMAL,
        )
        pump = Pump(transport=transport, address=cfg.address, reply_timeout_s=cfg.reply_timeout_s)

        # Each frame must carry '/3' as the address.
        transport.expect(b"/3Q\r", dt_reply(0x40))
        transport.expect(b"/3?23\r", dt_reply(0x40, EXPECTED_SOFTWARE_VERSION.encode("ascii")))
        transport.expect(b"/3?202\r", dt_reply(0x40, EXPECTED_SERIAL_NUMBER.encode("ascii")))
        transport.expect(b"/3?76\r", dt_reply(0x40, EXPECTED_CONFIG_BLOB.encode("ascii")))
        transport.expect(b"/3*\r", dt_reply(0x40, b"240"))
        transport.expect(b"/3?6\r", dt_reply(0x40, b"I"))
        transport.expect(b"/3?\r", dt_reply(0x40, b"0"))

        report = diagnose(pump)
        assert report.software_version == EXPECTED_SOFTWARE_VERSION
        assert report.serial_number == EXPECTED_SERIAL_NUMBER
        # Confirm the wire actually carried '/3', not '/1':
        for frame in transport.sent:
            assert frame[1:2] == b"3", f"address byte in {frame!r} is not '3'"
