"""Invalid progress structures, distinct from native file I/O failures."""


class ProgressConflictError(ValueError):
    """Progress or session state conflicts with the requested operation."""


class UnsupportedProgressError(ValueError):
    """Unmodeled progress cannot be safely combined by this operation."""


class BadProgressSave(ValueError):
    """Progress violates structure, identity or the requested normal-save read rules."""


class UnresolvedLevelIdentityError(BadProgressSave):
    """A Level's own SID or permitted vanilla numeric ID cannot identify it."""
