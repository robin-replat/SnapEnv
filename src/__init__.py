"""SnapEnv — Ephemeral preview environments for every Pull Request."""

from importlib.metadata import metadata

_meta = metadata("SnapEnv")

__version__ = _meta["Version"]
__description__ = _meta["Summary"]