# Source map — controller pins (2026-09-28)

`CONTROLLER_INPUTS` supersedes assignment snapshot pins and the earlier
compact handoff where they disagree. This branch is the assembly candidate.
It starts at public #106742 `cb8d6920549ebe9d31f69f187d30b356b5639eed` and does
not push to NousResearch.

Historical supplier proofs stay on `6f2aeb528fc6e8c9cee8ed1fa022a1c50c05829d`.
They are not results for cb8. The 6f2 candidate remains
`cursor/barryx-layers-integrate-52cc` at `5202c7d1f214b10d62a08e2e9c09c36c8bc4f2ce`.

| Role | Pin | Notes |
|---|---|---|
| Assembly target | `cb8d6920549ebe9d31f69f187d30b356b5639eed` | Public #106742. Fetch exact. Never float. |
| Supplier proof foundation | `6f2aeb528fc6e8c9cee8ed1fa022a1c50c05829d` | Historical recipes only. |
| Runtime #111216 | `041ae76f80ba2330ef6f1b7b961adc08e59a1877` | Public admission supplier. |
| Route #100016 | `8a29b6d13bc558221130b4216a93f373afa68caf` | Supersedes `1fa3c0addd0c3eec671f3019c443dd3e449db134`. Parent of 8a29 is 1fa3c0. |
| Output recipe/head | `23c5e66d0da21b46cc373993cd39dc2d7ab3929e` | `docs/layers-output-journey/`. Plan still names route 1fa3c0 and retention 004015d. |
| Output implementation | `31d00b0ed728e8d580aadf8a143cda3343752441` | Parent of the recipe commit. Not `4be9cb11031f3001929fd74cadb8f20b097cbf08`. |
| Input #111362 | `a05c7d2fc9c16f7d1a5d6bf8ecb8e6f3365ca462` | Unchanged. |
| Files transport #98072 | `9f143dedd82a6da452d20d1cace4869b23b14ada` | Not classic Files and not the desktop Files pin. |
| Retention #99107 | `c9f0029475f085e3b5e66b77df74cd5470925aef` | Accepted F1. Recipe still pins `004015d6087fe031231c4d7d9e0032cc59b679eb`. |
| Desktop #97846 | `5211890fb87b626c9ba569910375707530c3a2cc` | Not a full-tree overlay onto cb8. |
| Permission #111939 | `3b0d88e044f4a689def4ae1fee45186aafb51e11` | v2 control slice. Replaces v1 baseline `34332b47b3fb3c8394879e7179e157b89be115de` for Stop vs approval grant/revoke only. Not joined onto cb8. |
| Messaging #98073 | head `eea4a0c96d321bfd9a03705627f7f0d6a6280f40`; product `8f5338e6a7699c615112e2e2ef50136f31f8e466` | Private grammar. Recipe still bases on `d4d9f905e8123eea38ad81c4cdd6ac44257315d8`. Not joined onto cb8. |

Local-only Stop ACK `8461351e856450f33f6ed80a41b0d7e81b47fa59` and shipped-history
importer `cc69090b8875f6c338fccb09d45f0cf2dfa8440b` are evidence. They are not
copied onto this branch and are not a public supplier.

Client calls for the v2 control slice are in `PRIVATE_CONTROLS.md`.
Backend joins that are not clean public blobs are in `BACKEND_CONFLICTS.md`.
This branch does not edit `gateway/**`, `tui_gateway/**`, `hermes_state*.py`,
`agent/**`, `acp_adapter/**`, `tools/**`, or `hermes_cli/**`.
