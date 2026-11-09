"""Shared exception identities for scheduler adapters."""

class SafetyError(RuntimeError):
    pass


class PendingError(RuntimeError):
    pass


class TaskUnavailable(PendingError):
    """A positively unavailable task has not sent any irreversible action."""


class ArenaQuotaExhausted(TaskUnavailable):
    """All original actions settled and home verified; unmet rewards are explicit."""


class LeaseBlocked(RuntimeError):
    pass
