# Bundling skills with the MCP server

This document describes how predefined *skills* (Anthropic Agent Skills, the
`SKILL.md` format) are authored, where they live, and how they are distributed
so that they are discoverable.

## What a skill is (and what MCP is not)

A skill is a folder containing a `SKILL.md` file: YAML frontmatter (`name`,
`description`) plus a markdown body of instructions, optionally with supporting
`scripts/` and `references/`. Skills are consumed by the *agent runtime*
(Claude Code / Cowork), which reads `SKILL.md` from disk and decides when to
load it based on the `description`.

Important: **MCP has no native "skill" primitive.** The protocol exposes tools,
resources, and prompts only. An MCP server therefore cannot make a client
auto-load a skill over the wire. We support two distribution paths, and they
are complementary:

1. **Plugin bundling** — the native path for Claude Code / Cowork auto-discovery.
2. **MCP resource/prompt exposure** — lets *any* MCP client pull the skill text,
   even outside Claude Code.

## Where skills live (canonical location)

```
src/openswmm_mcp/skills/
  __init__.py                 # auto-discovery loader (skills_mcp sub-server)
  <skill-name>/
    SKILL.md                  # required: frontmatter + instructions
    scripts/                  # optional: helper scripts the skill references
    references/               # optional: long-form reference material
```

Skills live **inside the package** so they ship in the wheel as package data.
This is verified: a built wheel contains
`openswmm_mcp/skills/<skill-name>/SKILL.md`. No extra `pyproject.toml`
configuration is required — hatchling includes non-`.py` files under the
package automatically.

## Authoring a new skill

1. Create `src/openswmm_mcp/skills/<skill-name>/SKILL.md`.
2. Frontmatter must include `name` and a high-signal `description`. The
   description is the trigger: state when to use the skill *and* when not to,
   with concrete keywords. See `calibrate-model/SKILL.md` for the house style.
3. Write the body as an ordered, tool-driven workflow that references the
   actual MCP tool names (e.g. `lifecycle_open_model`,
   `geopackage_compare_sim_vs_observed`). Follow the project file-IO policy:
   intermediate outputs go to user-reviewable locations, not temp dirs.
4. Nothing else to wire up — the loader discovers it at import time.

## How skills are exposed over MCP

`src/openswmm_mcp/skills/__init__.py` defines a `skills_mcp` sub-server,
mounted (no namespace) in `server.py`. It scans `*/SKILL.md` once at import
and exposes:

- Resource `swmm://skills` — JSON index of `{name, description}`.
- Resource `swmm://skills/{skill_name}` — the full `SKILL.md` body.
- Prompt `use_skill(skill_name)` — injects a skill body into the conversation.

This makes skills discoverable from any MCP client (read the index resource,
then fetch a specific skill).

## Native auto-discovery via a Claude Code / Cowork plugin

For Claude Code / Cowork to auto-load these skills (so the agent picks them up
by `description` without being told), package the server as a plugin and point
the plugin's `skills/` at this directory. A plugin bundles the MCP server and
the skills together:

```
openswmm-plugin/
  .claude-plugin/plugin.json   # manifest; declares the MCP server
  skills/ -> ../src/openswmm_mcp/skills   # same skills, one source of truth
```

Keep `src/openswmm_mcp/skills/` as the single source of truth and reference it
from the plugin (symlink or build step) rather than duplicating `SKILL.md`
files. Confirm the current plugin manifest schema and skills-path convention
against the Claude Code plugin documentation before publishing, as the format
evolves.

## Summary

- Author skills as folders under `src/openswmm_mcp/skills/`.
- They ship in the wheel automatically and are exposed over MCP as resources +
  a prompt.
- For native agent auto-discovery, distribute the server as a plugin whose
  `skills/` points at the same directory.
