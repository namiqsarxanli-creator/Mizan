"""Shared foundations for every Mizan service."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mizan-core")
except PackageNotFoundError:  # pragma: no cover - running from a source tree without install
    __version__ = "0.0.0"

__all__ = ["__version__"]
