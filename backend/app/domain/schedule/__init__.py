"""Universal Clinical Trial Schedule Model (UCTSM)."""

from . import builtin_extensions  # noqa: F401  (registers built-in PROTOCOL_DEFINED handlers)
from .models import UniversalSchedule

__all__ = ["UniversalSchedule"]

