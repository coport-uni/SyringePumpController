"""Smoke tests for the `sy01b-diagnose` CLI.

Fake-pump tests were removed when the package collapsed onto the real pump as
ground truth. What remains here is the argument-parsing path that can be checked
without opening a serial port.
"""

from __future__ import annotations

import pytest

from sy01b.cli import diagnose as cli


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
