"""Provide the version of the package."""

from importlib import metadata

__version__ = metadata.version("jira_sync")
__version_info__ = tuple(int(x) for x in __version__.split("."))
