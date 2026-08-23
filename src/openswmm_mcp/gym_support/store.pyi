"""Typed stub for L{openswmm_mcp.gym_support.store}.

@author: Caleb Buahin
"""

import os
from pathlib import Path

from openswmm_mcp.gym_support.config import EnvConfig

class GymStore:
    """Named-config store persisted as one JSON file per config.

    @ivar config_dir: Directory holding C{<name>.json} files.
    @type config_dir: L{Path}
    """

    config_dir: Path

    def __init__(self, config_dir: str | os.PathLike) -> None:
        """
        @param config_dir: User-visible directory for config JSON files.
        @type config_dir: str or C{os.PathLike}
        """

    def save_config(self, name: str, config: EnvConfig, *, overwrite: bool = ...) -> Path:
        """Persist *config* as C{<config_dir>/<name>.json}.

        @raise ToolError: C{VALIDATION_ERROR} on invalid name or
            existing config without C{overwrite}.
        @return: The path written.
        @rtype: L{Path}
        """

    def get_config(self, name: str) -> EnvConfig:
        """Load and re-validate the named config from disk.

        @raise ToolError: C{ELEMENT_NOT_FOUND} when missing;
            C{VALIDATION_ERROR} when corrupt/invalid.
        @rtype: L{EnvConfig}
        """

    def list_configs(self) -> list[str]:
        """Return the names of all stored configs, sorted.

        @rtype: list of str
        """

    def delete_config(self, name: str) -> Path:
        """Delete the named config file.

        @raise ToolError: C{ELEMENT_NOT_FOUND} when missing.
        @return: The path removed.
        @rtype: L{Path}
        """
