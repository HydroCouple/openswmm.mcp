# Examples

Each example is a sequence of tool calls; arguments are shown as keyword
arguments. See the [tools guide](tools.md) for every tool.

## 1. Run a model and review results

```text
open_model(path="site_drainage.inp")                       # -> counts, options, window
run(session_id="default")                                  # -> finished, continuity errors
report(session_id="default", name="flooding", top=10)      # ranked flooded nodes
report(session_id="default", name="capacity", top=10)      # links by max filling (d/D)
timeseries(source="default", kind="node", ids=["J10"], variable="depth")
export(session_id="default", path="links.csv", kind="link",
       fields=["stats.max_flow", "stats.max_filling"])
session(action="close", session_id="default")
```

## 2. What-if scenario

```text
open_model(path="site_drainage.inp", session_id="base")
open_model(path="site_drainage.inp", session_id="upsize")
set(session_id="upsize", kind="link.xsect",
    changes=[{"id": "C10", "field": "g1", "value": 3.0}])
run(session_id="base")
run(session_id="upsize")
compare(a="base", b="upsize", kind="node", variable="depth")
report(session_id="upsize", name="flooding")
```

## 3. Runtime control during a run

```text
open_model(path="site_drainage.inp")
run(session_id="default", until="+1h")
call(session_id="default", target="forcing", method="node_lat_inflow",
     args={"node": "J1", "value": 20.0, "persist": true})
set(session_id="default", kind="link", changes=[{"id": "C5", "field": "target_setting", "value": 0.5}])
run(session_id="default")
```

## 4. Build a model from scratch

```text
open_model(session_id="new")                               # empty model, default options
call(session_id="new", target="options", method="set_item",
     args={"key": "FLOW_UNITS", "value": "CMS"})
edit(session_id="new", action="add", kind="node", ids=["J1"], type="JUNCTION",
     properties={"invert_elev": 10.0, "max_depth": 3.0})
edit(session_id="new", action="add", kind="node", ids=["O1"], type="OUTFALL",
     properties={"invert_elev": 9.0})
edit(session_id="new", action="add", kind="link", ids=["C1"], type="CONDUIT",
     properties={"from_node": "J1", "to_node": "O1", "length": 100.0, "roughness": 0.013})
set(session_id="new", kind="link.xsect", changes=[{"id": "C1", "field": "g1", "value": 1.0}])
save(session_id="new", path="new_model.inp")
run(session_id="new")
```

## 5. Discover what the engine offers

```text
describe()                                  # element kinds, services, enums
describe(topic="node", session_id="default")  # fields with units, methods, sub-views
describe(topic="surface2d.groundwater")     # a service
describe(topic="forcing.node_lat_inflow")   # one method's signature
describe(topic="enum:XSectShape")
find(session_id="default", kind="node", where="stats.max_depth > 2")
```
