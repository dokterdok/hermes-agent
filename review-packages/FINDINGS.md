# FINDINGS — adversarial review of draft #6

Reviewed revision: `af9b4fa324e9dea63f4d3e21c0fc46d80ff70525` (`review-packages/A2-RUNTIME-106742-CLOSEOUT.md` only).
Live re-check: 2026-09-26, NousResearch/hermes-agent#106742 head `d7f5c13d73784b4e536bf6fe0d20a48089523ec0`, main `d0288be5b3330d2442e3907185b8e9d0958297bb`, #120652 head `38da16a41cb83bf809d3531fde197c5e77a8c475` still OPEN, #121813 head `6ae81b2f4ccdaf98d84da5bd9edd419a690548bf` still OPEN.

No product code was in the reviewed revision. Findings are about the review package.

## F1 — quoted “exact diff” does not apply

The package called the fenced diff the output of `git diff d7f5c13 d9dfbbb` and told a reader to use it. The fence was produced with `str.rstrip()` on the patch. The real diff ends in two single-space context lines. Stripping them makes `git apply --check` report `corrupt patch at line 865`. The unstripped diff (`48524` bytes, SHA-256 `09f944383f31934ec8086e4afec154321489fd9bf57a4e10c7f1a69792beb4ef`) applies onto a detached checkout of `d7f5c13`.

Disposition: **fixed**. The fence in the corrected package is byte-identical to that git diff. `git apply --check` on `/tmp/tip-apply` (detached `d7f5c13`) exits 0 and leaves the worktree clean.

## F2 — three-dot patch described as interchangeable with the tip patch

The package said the three-dot `d7f5c13...38da16a` and the tip merge diff differ only in git index lines and hunk line numbers. After dropping `index` and `@@` lines, the texts still differ in `hermes_cli/config_migrations.py` context: the tip has a comment immediately after the migration tuple; the three-dot context is a blank line. `git apply --check` of the three-dot patch onto `d7f5c13` fails at `hermes_cli/config_migrations.py:765`. Added lines and the `+501/−52` stat match. The patches are not substitutes.

Disposition: **fixed**. The package now says to apply the tip diff, and records the three-dot apply failure.

## F3 — `Outcome: PASS` overstated the milestone

`Outcome: PASS` in the package RESULT read as closeout acceptance. The overnight COMPLETE claim was already rejected in prose, but the RESULT line reintroduced a pass for a milestone that is still open, dirty, and red on its CI gate. The 274-pass is evidence for one local tree only.

Disposition: **fixed**. RESULT is `Outcome: BLOCKED` and `Adversarial review: HELD`.

## F4 — rebase written as the only closure blocker; failed jobs called “required”

`Exact blocker` named only the dirty rebase. On `d7f5c13` the rollup check `All required checks pass` is `failure`. `.github/workflows/ci.yaml` on that commit says branch protection should require only that rollup. Failed jobs observed on the same commit: Desktop core E2E (Linux), JS & TS checks, Windows E2E (real processes), Python tests / Run tests, Python tests / e2e, Python tests / e2e-upgrade. A clean rebase would not carry the local 274-pass forward as that gate. This review did not read live branch-protection rules, so it does not claim each failed job is itself a protected context.

Disposition: **fixed**. Holds (1) and (2) split the conflict rebase from the CI gate.

## F5 — “do not merge onto main” did not say which main

Draft #6 targets `dokterdok/hermes-agent` `main` at `057dcdf236f8a6a26721c10fcc6ccb72726e272a`. That is not Nous `main` `d0288be`. Merging #6 would add markdown to the stale fork branch. It would not update NousResearch/hermes-agent, would not land #120652, and would not clear #106742’s dirty state. The old package said not to fast-forward the document onto “product main” without naming the fork base.

Disposition: **fixed**. Publication section names both SHAs and says not to merge #6.

## F6 — conflict command used a local ref

The hold cited `git merge-tree --write-tree refs/review/main-20260926 d7f5c13`. That ref exists only in the reviewer’s clone. Re-running with the public OID `d0288be5b3330d2442e3907185b8e9d0958297bb` still exits 1 with the same 51 conflict paths.

Disposition: **fixed**. The command in the package uses the two public OIDs.

## F7 — GitHub `mergeable=true` / `unstable` over-explained

The package said GitHub `mergeable=true` “matches” the clean tip merge and that `unstable` “matches the absence of checks.” #120652 `updated_at` is `2026-09-24T04:45:58Z`, before tip commit time `2026-09-24T17:14:57Z`, and API `base.sha` is still `26b02651`. Today’s GET does return `mergeable=true`, which agrees with local merge-tree, but the stale `updated_at` and `base.sha` are not evidence the flag was recomputed when the tip moved. Combined commit status on `38da16a` is `pending` with zero statuses. `unstable` is GitHub’s state for that pending status, not a defined synonym for “no checks.”

Disposition: **fixed**. The package separates merge-tree (clean, tree `d9dfbbb`), today’s `mergeable=true`, and the pending empty status list.

## F8 — “each still absent” overclaimed #121813 behavior

The #121813 section said each of the ten behaviors is still absent on `d7f5c13`. The evidence is that `git merge-tree --write-tree d7f5c13 6ae81b2f` is clean and the diff stat against the tip is still `+600/−52` across 24 files (tree `1fb2e90`). That shows the published edits are not already in those files. It does not show each behavior failing on the tip. #121813’s “38 passed” was not re-run.

Disposition: **fixed**. Wording is now the patch stat, not a behavioral replay.

## Checked and not changed

- #106742 head, dirty/mergeable, and live main OID match a fresh pulls/commits API read.
- `38da16a` is still not an ancestor of `d7f5c13`. #120652 and #121813 are still OPEN.
- Tip `DEFAULT_CONFIG["_config_version"]` is 47 and `MIGRATIONS` has no step 48.
- Tested commit `5ba3844^{tree}` is `d9dfbbb`.
- `KANBAN_RATE_LIMIT_EXIT_CODE` is 75 and `KANBAN_TERMINAL_PROVIDER_EXIT_CODE` is 78 on that tree. `kanban_db.py` is not in the 18-file diff, so those numbers are the tip’s constants.
- `_isolate_hermes_home` is `autouse=True` on `d7f5c13`.
- Per-file pass counts 2+2+9+2+3+17+5+1+22+4+169+6+4+19+9 = 274, plus 4 windows_only skips in `test_config.py`.
- Linking comment `5802886339` does say nothing was merged or deployed.
- Stacking #121813 onto the #120652 merge still yields tree `11653ac38df89a3c59f987581bb0098d03c9e1ae`.

## Re-review

Corrected package re-read against the same live pins.

- Embedded diff SHA-256 matches `/tmp/fresh.diff` and `git apply --check` exits 0.
- RESULT no longer says `Outcome: PASS`.
- Holds name the upstream rebase, the red rollup, and the unmerged #120652 as separate actions.
- No remaining in-scope defect in the document.

Adversarial review: **HELD**

Held actions, exact:

1. NousResearch maintainer merges or rebases `main` `d0288be5b3330d2442e3907185b8e9d0958297bb` into `feat/unified-gateway-runtime` `d7f5c13d73784b4e536bf6fe0d20a48089523ec0` and pushes that branch. Until then #106742 stays `mergeable=false` / `dirty` (51 conflicts).
2. On the head that push produces, `All required checks pass` has to be green. It is `failure` on `d7f5c13`. This review does not triage those jobs.
3. Maintainer of `feat/unified-gateway-runtime` decides whether to merge #120652 (`38da16a`) into that branch. Content merge onto `d7f5c13` is clean. This agent does not merge it. That merge does not land #106742 on `main`.

Do not merge dokterdok/hermes-agent#6. Do not push or comment on NousResearch #106742, #120652, #121813, #111216, #100016, or #97846.
