"""Single-class façade for the SY-01B controller."""

from sy01b.syringe_pump_controller import SyringePumpController

__version__ = SyringePumpController.__version__
__all__ = ["SyringePumpController", "__version__"]
