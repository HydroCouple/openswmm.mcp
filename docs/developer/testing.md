# Testing

Tests use [pytest](https://docs.pytest.org/) with `pytest-asyncio` and drive the
real, compiled `openswmm` engine; there is no mock engine. `tests/conftest.py`
imports `openswmm.engine` unconditionally, so a missing engine fails the suite
rather than skipping it.

## Layout

```
tests/
  conftest.py                   # imports the engine (fail, don't skip)
  data/site_drainage_example.inp
  unit/
    conftest.py                 # output_dir, inp_path, session_manager, tools (in-memory client)
    data/                       # site_drainage_model.inp, twod_example.inp, street_inlet_junction.inp
    test_server_budget.py       # tool count and definition size caps; toolsets; run_python gating;
                                #   a real stdio handshake, launched as a desktop client would
    test_catalog_contract.py    # every catalogued field readable via get; writable scalars round-trip
    test_method_contract.py     # every catalogued method callable with the catalog's signature
    test_skill_walkthroughs.py  # each bundled skill's tool sequence, end to end
    test_workflows.py           # open/run/report/timeseries/compare/export/save/session/edit/call
    test_code_and_resources.py  # run_python, resources, prompts, skills name only real tools/fields
    test_gym_*.py               # gym config/registry/store/envs/jobs/runs/scoring
    test_auth.py                # OAuth, JWT, MultiAuth, stdio bypass
```

Everything a test writes lands under `tests/_output/<module>/<test>/`, which
is replaced on each run and left in place for review.

## Calling tools

The `tools` fixture connects an in-memory FastMCP `Client` to a fresh core
server whose working directory is the test's output folder:

```python
async def test_flooding(tools, inp_path):
    await tools("open_model", path=inp_path)
    await tools("run", session_id="default")
    rows = (await tools("report", session_id="default", name="flooding"))["rows"]
```

## Running

```bash
pytest tests/unit -q                  # full suite (needs the compiled engine)
pytest tests/unit -q -k "not gym"     # without the gym extra
```


## Coverage means dispatch, not every numerical scenario

Run `test_catalog_contract.py`, `test_method_contract.py` and `test_gym_envs.py`
against the same rebuilt engine used by the server. The method contract includes
expected validation/lifecycle refusals; passing it does not mean every method
completed a physically meaningful simulation. Keep successful model-specific
round trips for newly added tables, forcing, persistence and optional features.
The catalog must be regenerated in the engine repository before these checks.
