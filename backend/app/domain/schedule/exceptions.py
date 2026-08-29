class ScheduleDomainError(ValueError):
    """Base error for deterministic schedule domain failures."""


class ScheduleNotApprovedError(ScheduleDomainError):
    """Raised when patient evaluation is attempted for a non-approved schedule."""


class ImmutableScheduleError(ScheduleDomainError):
    """Raised when an approved schedule is modified."""


class UnsupportedTimingError(ScheduleDomainError):
    """Raised when a timing expression has no deterministic implementation."""

