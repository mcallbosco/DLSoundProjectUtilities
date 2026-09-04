"""Errors that can be displayed directly by the application and its CLIs."""


class BaselineError(RuntimeError):
    """Invalid baseline input or failed content generation."""


class VpkPipelineError(RuntimeError):
    """Invalid VPK input or failed extraction/parsing."""
