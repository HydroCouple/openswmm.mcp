"""JSON-on-disk persistence for named environment configs (plan §7.1).

Each named L{EnvConfig<openswmm_mcp.gym_support.config.EnvConfig>} is
persisted as C{<config_dir>/<name>.json} in a user-visible directory
(CLAUDE.md §4.1) and reloaded on demand — the disk *is* the store, so
configs survive server restarts and users can review, edit, and
version them directly.

@author: Caleb Buahin
@copyright: Copyright (c) 2026 Caleb Buahin
@license: MIT
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from pydantic import ValidationError

from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.gym_support.config import EnvConfig

#: Allowed config names: filesystem-safe, no path separators or dots-only.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class GymStore:
    """Named-config store persisted as one JSON file per config.

    Stateless between calls: every operation reads from / writes to
    C{config_dir}, so multiple server processes pointed at the same
    directory observe each other's configs and a restart loses nothing.

    @ivar config_dir: Directory holding C{<name>.json} files; created
        on first write.
    @type config_dir: L{Path}
    """

    def __init__(self, config_dir: str | os.PathLike) -> None:
        """
        @param config_dir: Directory for config JSON files. Should be
            user-visible (e.g. C{<inp_dir>/gym_configs}); never a temp
            dir (CLAUDE.md §4.1).
        @type config_dir: str or C{os.PathLike}
        """
        self.config_dir = Path(config_dir)

    # -- helpers -----------------------------------------------------------

    def _path(self, name: str) -> Path:
        """Return the JSON path for *name*, validating the name.

        @param name: Config name.
        @type name: str
        @return: C{<config_dir>/<name>.json}.
        @rtype: L{Path}
        @raise ToolError: C{VALIDATION_ERROR} when *name* is not a safe
            filename component.
        """
        if not _NAME_RE.match(name):
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] Invalid config name '{name}'. "
                "Names must start with a letter or digit and contain only "
                "letters, digits, '_', '-', and '.'."
            )
        return self.config_dir / f"{name}.json"

    # -- CRUD ---------------------------------------------------------------

    def save_config(self, name: str, config: EnvConfig, *, overwrite: bool = False) -> Path:
        """Persist *config* as C{<config_dir>/<name>.json}.

        @param name: Config name (filesystem-safe; see L{_path}).
        @type name: str
        @param config: The validated config to persist.
        @type config: L{EnvConfig}
        @param overwrite: Allow replacing an existing config of the
            same name. Defaults to C{False}.
        @type overwrite: bool
        @return: The path written, so tools can report it to the user.
        @rtype: L{Path}
        @raise ToolError: C{VALIDATION_ERROR} when the name is invalid
            or already exists and *overwrite* is C{False}.
        """
        path = self._path(name)
        if path.exists() and not overwrite:
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] Config '{name}' already exists "
                f"at {path}. Pass overwrite=true to replace it."
            )
        self.config_dir.mkdir(parents=True, exist_ok=True)
        payload = {"name": name, "config": config.model_dump(mode="json")}
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path

    def get_config(self, name: str) -> EnvConfig:
        """Load and re-validate the named config from disk.

        @param name: Config name.
        @type name: str
        @return: The stored config.
        @rtype: L{EnvConfig}
        @raise ToolError: C{ELEMENT_NOT_FOUND} when no such config
            exists; C{VALIDATION_ERROR} when the file is corrupt or no
            longer satisfies the schema (e.g. hand-edited badly).
        """
        path = self._path(name)
        if not path.exists():
            available = ", ".join(self.list_configs()) or "<none>"
            raise ToolError(
                f"[{ErrorCode.ELEMENT_NOT_FOUND}] No config named '{name}' in "
                f"{self.config_dir}. Available: {available}."
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return EnvConfig(**payload["config"])
        except (json.JSONDecodeError, KeyError, TypeError, ValidationError) as exc:
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] Config file {path} is corrupt "
                f"or invalid: {exc}. Fix or delete the file and re-create the "
                "config."
            ) from exc

    def list_configs(self) -> list[str]:
        """Return the names of all stored configs, sorted.

        Unreadable files are *listed* (their names are real); errors
        surface when the config is actually loaded via L{get_config}.

        @return: Sorted config names (file stems).
        @rtype: list of str
        """
        if not self.config_dir.is_dir():
            return []
        return sorted(p.stem for p in self.config_dir.glob("*.json"))

    def delete_config(self, name: str) -> Path:
        """Delete the named config file.

        @param name: Config name.
        @type name: str
        @return: The path that was removed.
        @rtype: L{Path}
        @raise ToolError: C{ELEMENT_NOT_FOUND} when no such config exists.
        """
        path = self._path(name)
        if not path.exists():
            available = ", ".join(self.list_configs()) or "<none>"
            raise ToolError(
                f"[{ErrorCode.ELEMENT_NOT_FOUND}] No config named '{name}' in "
                f"{self.config_dir}. Available: {available}."
            )
        path.unlink()
        return path
