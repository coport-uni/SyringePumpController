"""Smoke tests for the `sy01b-diagnose` CLI.

The CLI's actual serial open is impossible to exercise without hardware; these
tests cover the argument-parsing + config-resolution paths and one end-to-end
run with a stubbed Pump that bypasses the real transport.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sy01b.cli import diagnose as cli
from sy01b.diagnostics import DiagnosticsReport
from sy01b.errors import LowSupplyVoltageError
from sy01b.protocol import StatusByte


class TestHelp:
    def test_help_exits_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as ei:
            cli.main(["--help"])
        assert ei.value.code == 0
        out = capsys.readouterr().out
        assert "sy01b-diagnose" in out


class TestConfigResolution:
    def test_requires_config_or_port(self) -> None:
        with pytest.raises(SystemExit) as ei:
            cli.main([])
        assert ei.value.code != 0

    def test_port_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def fake_open(cfg: Any) -> Any:
            captured["cfg"] = cfg
            raise SystemExit(99)

        monkeypatch.setattr(cli.Pump, "open", classmethod(lambda cls, cfg: fake_open(cfg)))
        with pytest.raises(SystemExit) as ei:
            cli.main(["--port", "/dev/ttyUSB0"])
        assert ei.value.code == 99
        assert captured["cfg"].port == "/dev/ttyUSB0"

    def test_toml_with_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        toml = tmp_path / "pump.toml"
        toml.write_text('[pump]\nport = "/dev/ttyUSB9"\naddress = 1\n', encoding="utf-8")

        captured: dict[str, Any] = {}

        def fake_open(cfg: Any) -> Any:
            captured["cfg"] = cfg
            raise SystemExit(99)

        monkeypatch.setattr(cli.Pump, "open", classmethod(lambda cls, cfg: fake_open(cfg)))
        with pytest.raises(SystemExit):
            cli.main(["--config", str(toml), "--address", "5"])
        assert captured["cfg"].port == "/dev/ttyUSB9"
        assert captured["cfg"].address == 5


class TestRun:
    @pytest.fixture
    def fake_report(self) -> DiagnosticsReport:
        return DiagnosticsReport(
            software_version="V1.4",
            serial_number="SN-TEST",
            config="cfg",
            supply_volts=24.0,
            valve_position="I",
            plunger_steps=0,
            pre_init_status=StatusByte.decode(0x47),
            warnings=(),
        )

    def test_happy_path_exits_zero_and_prints_report(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
        fake_report: DiagnosticsReport,
    ) -> None:
        class _StubPump:
            def __init__(self) -> None:
                pass

            def __enter__(self) -> Any:
                return self

            def __exit__(self, *a: object) -> None:
                pass

        monkeypatch.setattr(cli.Pump, "open", classmethod(lambda cls, cfg: _StubPump()))
        monkeypatch.setattr(cli, "diagnose", lambda pump: fake_report)

        rc = cli.main(["--port", "/dev/ttyUSB0"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "V1.4" in out
        assert "SN-TEST" in out

    def test_low_voltage_exits_two(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _StubPump:
            def __enter__(self) -> Any:
                return self

            def __exit__(self, *a: object) -> None:
                pass

        def _fail(_pump: Any) -> None:
            raise LowSupplyVoltageError(measured_v=15.0, min_v=22.0)

        monkeypatch.setattr(cli.Pump, "open", classmethod(lambda cls, cfg: _StubPump()))
        monkeypatch.setattr(cli, "diagnose", _fail)

        rc = cli.main(["--port", "/dev/ttyUSB0"])
        assert rc == 2
        err = capsys.readouterr().err
        assert "DIAGNOSTIC FAILED" in err
