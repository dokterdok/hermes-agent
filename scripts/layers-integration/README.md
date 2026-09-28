# Layers integration notes

Consumer lane only. Do not use these notes as a patch for `gateway/`,
`tui_gateway/`, `hermes_state*.py`, `agent/`, `acp_adapter/`, `tools/`, or
`hermes_cli/`.

- Pin table: `docs/layers-integration/SOURCE_MAP.md`
- Conflicts returned to Barry: `docs/layers-integration/BACKEND_CONFLICTS.md`
- Journey status: `docs/layers-integration/JOURNEY.md`

`tests/layers_integration/test_controller_pins.py` checks that this branch
still contains cb8 and that the owned backend blobs above are unchanged.
Route `8a29b6d` and retention `c9f0029` parent checks run only when those
objects are already in the local object store. They are not ancestors of cb8.
