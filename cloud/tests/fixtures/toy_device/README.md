# Toy device repo (cloud test fixture)

A stand-in for `device/` until the device team's code lands (build plan B1). It proves the
coder pipeline (M8, flow F11):

- `control/hold.py` carries fault D1: it opens the gripper when something comes within 50 mm
  while holding.
- `tests/` is the device test suite. It passes on the buggy code, as a real suite would
  before the regression test exists.
- `tasks/d1_drop_on_proximity/verify.py` is the fault catalog's hidden check
  (`docs/fault-catalog.md`). It fails until D1 is fixed.

The contracts example patch (`packages/contracts/examples/incident_d1_code_patch.json`)
applies to it unchanged. Run its tests from this folder with `python -m pytest -q`; the
cloud's own pytest skips this folder (`../conftest.py`).
