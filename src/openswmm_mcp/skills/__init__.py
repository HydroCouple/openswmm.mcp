"""Auto-discovered SWMM Agent Skills, exposed over MCP.

Skills are folder-based: each lives at ``<skill-name>/SKILL.md`` inside this
package, optionally alongside supporting ``scripts/`` or ``references/`` files.
They ship in the wheel as package data (see ``pyproject.toml``) so they travel
with the server.

Because MCP has no native "skill" primitive, skills are surfaced two ways:

* As **resources** -- ``swmm://skills`` lists them and
  ``swmm://skills/{skill_name}`` returns a skill's full ``SKILL.md`` body. This
  works for any MCP client.
* As a **prompt** -- ``use_skill(skill_name)`` injects the skill body into the
  conversation for clients with a prompt UI.

For native auto-loading in Claude Code / Cowork, this same directory is bundled
into the plugin's ``skills/`` path; see ``docs/developer/SKILLS.md``.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastmcp import FastMCP

from openswmm_mcp.errors import ToolError

skills_mcp = FastMCP("skills")

_SKILLS_DIR = Path(__file__).parent


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Return the top-level YAML frontmatter keys (name, description, ...).

    A deliberately small parser: skill frontmatter is flat ``key: value`` pairs,
    so this avoids a YAML dependency. Quoted values are unquoted.
    """
    meta: dict[str, str] = {}
    if not text.startswith("---"):
        return meta
    end = text.find("\n---", 3)
    if end == -1:
        return meta
    for line in text[3:end].splitlines():
        if not line or line.startswith((" ", "\t")) or ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta


def _discover() -> dict[str, dict]:
    """Scan this package for ``<name>/SKILL.md`` and index them by folder name."""
    skills: dict[str, dict] = {}
    for skill_md in sorted(_SKILLS_DIR.glob("*/SKILL.md")):
        name = skill_md.parent.name
        body = skill_md.read_text(encoding="utf-8")
        meta = _parse_frontmatter(body)
        skills[name] = {
            "name": meta.get("name", name),
            "description": meta.get("description", ""),
            "body": body,
        }
    return skills


# Discovered once at import time -- skills are static package data.
_SKILLS = _discover()


@skills_mcp.resource("swmm://skills")
async def list_skills() -> str:
    """Index of bundled SWMM skills (name + description)."""
    return json.dumps(
        [{"name": s["name"], "description": s["description"]} for s in _SKILLS.values()],
        indent=2,
    )


@skills_mcp.resource("swmm://skills/{skill_name}")
async def get_skill(skill_name: str) -> str:
    """Return the full ``SKILL.md`` body for *skill_name*."""
    skill = _SKILLS.get(skill_name)
    if skill is None:
        available = ", ".join(_SKILLS) or "(none)"
        raise ToolError(f"Unknown skill '{skill_name}'. Available: {available}")
    return skill["body"]


@skills_mcp.prompt()
def use_skill(skill_name: str) -> str:
    """Inject a bundled skill's instructions into the conversation.

    Parameters
    ----------
    skill_name:
        Folder name of the skill (see the ``swmm://skills`` resource).
    """
    skill = _SKILLS.get(skill_name)
    if skill is None:
        available = ", ".join(_SKILLS) or "(none)"
        raise ToolError(f"Unknown skill '{skill_name}'. Available: {available}")
    return skill["body"]
