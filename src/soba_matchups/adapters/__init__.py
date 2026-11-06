"""Product-specific reference readers and matching rules."""


class SkippableReference(ValueError):
    """A reference file with nothing to match or that cannot be read; the run goes on without it."""
