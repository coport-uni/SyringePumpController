"""Tests for Pump.Config — validation and TOML loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from sy01b import Pump


class TestValidation:
    def test_defaults_accepted(self) -> None:
        cfg = Pump.Config(port="/dev/ttyUSB0")
        assert cfg.address == 1
        assert cfg.baud == 9600
        assert cfg.syringe_uL == 5000
        assert cfg.step_mode is Pump.StepMode.NORMAL

    @pytest.mark.parametrize("bad_addr", [0, -1, 16, 100])
    def test_address_out_of_range_raises(self, bad_addr: int) -> None:
        with pytest.raises(ValueError, match="address"):
            Pump.Config(port="x", address=bad_addr)

    def test_unsupported_syringe_raises(self) -> None:
        with pytest.raises(ValueError, match="syringe_uL"):
            Pump.Config(port="x", syringe_uL=750)

    @pytest.mark.parametrize("syr", sorted(Pump.ALLOWED_SYRINGES_UL))
    def test_every_allowed_syringe_is_acceptable(self, syr: int) -> None:
        cfg = Pump.Config(port="x", syringe_uL=syr)
        assert cfg.syringe_uL == syr

    def test_invalid_baud_raises(self) -> None:
        with pytest.raises(ValueError, match="baud"):
            Pump.Config(port="x", baud=115200)

    def test_zero_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="reply_timeout_s"):
            Pump.Config(port="x", reply_timeout_s=0)


class TestStallCurrentOperand:
    """Operand table from CLAUDE.md: 25 uL -> 4, 50..1250 -> 5, 2500/5000 -> 6."""

    @pytest.mark.parametrize(
        ("syr", "operand"),
        [
            (25, 4),
            (50, 5),
            (1250, 5),
            (2500, 6),
            (5000, 6),
        ],
    )
    def test_table(self, syr: int, operand: int) -> None:
        cfg = Pump.Config(port="x", syringe_uL=syr)
        assert cfg.stall_current_operand() == operand


class TestStepMode:
    def test_normal_stroke(self) -> None:
        assert Pump.StepMode.NORMAL.full_stroke_steps == 12_000

    def test_fine_stroke(self) -> None:
        assert Pump.StepMode.FINE.full_stroke_steps == 96_000

    def test_micro_stroke(self) -> None:
        assert Pump.StepMode.MICRO.full_stroke_steps == 96_000


class TestTomlLoading:
    def test_loads_pump_section(self, tmp_path: Path) -> None:
        toml = tmp_path / "pump.toml"
        toml.write_text(
            '[pump]\nport = "/dev/ttyUSB2"\naddress = 3\nbaud = 38400\n'
            'syringe_uL = 1000\nstep_mode = "N1"\nreply_timeout_s = 2.5\n',
            encoding="utf-8",
        )
        cfg = Pump.Config.from_toml(toml)
        assert cfg.port == "/dev/ttyUSB2"
        assert cfg.address == 3
        assert cfg.baud == 38400
        assert cfg.syringe_uL == 1000
        assert cfg.step_mode is Pump.StepMode.FINE
        assert cfg.reply_timeout_s == 2.5

    def test_loads_top_level_keys(self, tmp_path: Path) -> None:
        toml = tmp_path / "pump.toml"
        toml.write_text('port = "/dev/ttyUSB3"\n', encoding="utf-8")
        cfg = Pump.Config.from_toml(toml)
        assert cfg.port == "/dev/ttyUSB3"

    def test_ignores_unknown_keys(self, tmp_path: Path) -> None:
        toml = tmp_path / "pump.toml"
        toml.write_text('[pump]\nport = "/dev/ttyUSB0"\nfuture_field = 42\n', encoding="utf-8")
        cfg = Pump.Config.from_toml(toml)
        assert cfg.port == "/dev/ttyUSB0"
