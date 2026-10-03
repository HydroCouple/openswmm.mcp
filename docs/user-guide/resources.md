# Resources

Resources give read-only access to the engine catalog, open sessions and the
bundled skills, using the `swmm://` URI scheme. Reading a resource costs no
tool definition.

| URI | Contents |
|---|---|
| `swmm://catalog` | Every target the engine exposes (element kinds, sub-views, services, standalone targets) with its class and a one-line description |
| `swmm://catalog/{target}` | Full catalog entries for one target, e.g. `swmm://catalog/node` or `swmm://catalog/forcing`: each field's type, units, access and lifecycle phases, each method's parameters |
| `swmm://sessions` | Open sessions with their state and input file |
| `swmm://session/{session_id}/summary` | Object counts, key options, simulation window and file paths for one session |
| `swmm://skills` | Index of the bundled skills (`name`, `description`) |
| `swmm://skills/{skill_name}` | A skill's full `SKILL.md` body |

The `describe` tool returns the same catalog information, filtered and with
units resolved to a session's unit system.
