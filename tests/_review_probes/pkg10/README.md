# Package 10 review probes (not for merge)

Probe tests behind the review comments on the seven package 10 lane PRs (#140967–#140973, issue #140719), 18 Oct 2026. Each probe asserts the *correct* behaviour, so it fails on the PR head where the review reports a bug. Not every case of every file is a finding: some files carry passing controls alongside the failing case.

Reviewed heads, compared against `main` `d24fe22029` and against all seven lanes merged onto `main`:

| Lane | PR | Head | Probes |
| --- | --- | --- | --- |
| 1, gateway process | #140967 | `6bbaa79050` | `lane1/` |
| 2, clients | #140968 | `7ce31c0192` (still reproduces on `cd8a1e6d7f`) | `lane2/` |
| 2, the RT-37 owner forward | #140968 | `cd8a1e6d7f` (`40cdeb6dd6`, `4fa480c50f`, `cd8a1e6d7f`) | `lane2-rt37/` |
| 3a, settings | #140969 | `bee90c2e12` | `lane3a/` |
| 3b, grants and Docker | #140970 | `98bd08e5cd` | `lane3b/` |
| 4a, identity and history | #140971 | `515fc35607` | `lane4a/` |
| 4b, session lists | #140972 | `fe2d54ecf2` | `lane4b/` (+ `apps/desktop/src/__review_probes__/pkg10/lane4b/` for vitest) |
| 5, messaging and Bot Chat | #140973 | `655aef56d8` | `lane5/` |

## Run

Copy a lane's directory into a checkout of that lane's head at the same path, then, with an isolated `HERMES_HOME`:

```bash
scripts/run_tests.sh tests/_review_probes/pkg10/lane5/ -rA --tb=short
```

Desktop probes (lane 4b): `cd apps/desktop && npx vitest run src/__review_probes__/pkg10/lane4b/`. `lane4b/test_zz_probe_desktop.py` drives its own vitest harness (`vitest.review.mts`) and needs `node_modules` (`LEFTHOOK=0 npm ci --ignore-scripts`).

Some probes import fixtures from the lane's own new tests, so they collect only on that lane's head (or a tree that contains it). A few `print` the measured values; run with `-s` to see them.

Two independent review rounds produced these: GPT-6 Astra (Codex CLI, reasoning `xhigh`) and Claude Opus 5.5. Where both wrote a probe for the same point, both are kept (`_2` suffix on a name clash).
