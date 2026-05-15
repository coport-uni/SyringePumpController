"""PumpConfig dataclass and StepMode enum."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

ALLOWED_SYRINGES_UL: frozenset[int] = frozenset(
    {25, 50, 100, 125, 250, 500, 1000, 1250, 2500, 5000}
)


class StepMode(StrEnum):
    NORMAL = "N0"
    FINE = "N1"
    MICRO = "N2"

    @property
    def full_stroke_steps(self) -> int:
        return 12_000 if self is StepMode.NORMAL else 96_000


_STALL_CURRENT_TABLE: tuple[tuple[int, int], ...] = (
    # (max_syringe_uL_inclusive, U200 operand)
    (25, 4),
    (1250, 5),
    (5000, 6),
)


@dataclass(frozen=True, slots=True)
class PumpConfig:
    port: str
    address: int = 1
    baud: int = 9600
    syringe_uL: int = 5000
    step_mode: StepMode = StepMode.NORMAL
    reply_timeout_s: float = 1.0

    def __post_init__(self) -> None:
        if not 1 <= self.address <= 15:
            raise ValueError(f"address must be 1..15, got {self.address}")
        if self.syringe_uL not in ALLOWED_SYRINGES_UL:
            raise ValueError(f"syringe_uL={self.syringe_uL} not in {sorted(ALLOWED_SYRINGES_UL)}")
        if self.baud not in (9600, 38400):
            raise ValueError(f"baud must be 9600 or 38400, got {self.baud}")
        if self.reply_timeout_s <= 0:
            raise ValueError(f"reply_timeout_s must be positive, got {self.reply_timeout_s}")

    @classmethod
    def from_toml(cls, path: Path) -> PumpConfig:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        section = data.get("pump", data)
        kwargs: dict[str, object] = {
            k: v for k, v in section.items() if k in cls.__dataclass_fields__
        }
        step = kwargs.get("step_mode")
        if isinstance(step, str):
            kwargs["step_mode"] = StepMode(step)
        return cls(**kwargs)  # type: ignore[arg-type]

    def stall_current_operand(self) -> int:
        for upper, operand in _STALL_CURRENT_TABLE:
            if self.syringe_uL <= upper:
                return operand
        raise ValueError(f"no stall-current entry for syringe_uL={self.syringe_uL}")
