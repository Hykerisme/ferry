"""Ferry build-deploy orchestrator."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ferry-tool")
except PackageNotFoundError:  # running from a source checkout without installation
    __version__ = "0.1.0"
