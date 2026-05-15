"""Single-class façade for the SY-01B controller."""

from sy01b.pump import Pump

__version__ = Pump.__version__
__all__ = ["Pump", "__version__"]
