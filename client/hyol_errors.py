"""Shared exception identities for scheduler adapters."""

class SafetyError(RuntimeError):
    pass


class PendingError(RuntimeError):
    pass


class LeaseBlocked(RuntimeError):
    pass
